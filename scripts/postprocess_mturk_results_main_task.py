import json
import os.path
import random
import sys
from itertools import groupby
from typing import Any, List, Dict

import boto3
import click
import pandas as pd
from loguru import logger
from tqdm import tqdm
import requests
from unidecode import unidecode
from urllib3.exceptions import RequestError
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()


def maybe_bool(b):
    if b == '1':
        return True
    if b == '2':
        return False
    return maybe_int(b)

def maybe_int(i):
    try:
        return int(i)
    except:
        return i


def postprocess_answer(answers, *fields, num=(1, 11)):
    print(answers)
    print([[(k,v) for k, v in answers[f"{f}{i}"].items() if v] for f in fields for i in range(*num)])
    return {
        f"{f}_{i}": next((maybe_bool(k) if len(answers[f"{f}{i}"]) == 2 else maybe_int(k) for k, v in answers[f"{f}{i}"].items() if v),-1) for f in fields for i in range(*num)
    }


AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")


def process_worker_answers(r: Dict[str, Any], answer_fields):
    worker_answers = postprocess_answer(
        json.loads(r["Answer.taskAnswers"])[0], *answer_fields, num=(1, 2)
    )
    return worker_answers


@click.command()
@click.argument("infile", default=sys.stdin)
@click.argument("outfile", type=str)
@click.option("--log-level", default="INFO")
@click.option("--answer-fields", "-af", default="contra-article,contra-self,rate,verdict,convince,new")
@click.option("--processed-assignments", '-pa', type=click.Path(file_okay=True, dir_okay=False),
              default='processed-main.json')
@click.option("--release", is_flag=True, default=False)
@click.option("--dry", is_flag=True, default=False)
def main(infile, outfile, processed_assignments, log_level, answer_fields, release, dry):
    endpoint_url = (
        "https://mturk-requester-sandbox.us-east-1.amazonaws.com"
        if not release
        else "https://mturk-requester.us-east-1.amazonaws.com"
    )

    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    assert AWS_ACCESS_KEY_ID, AWS_ACCESS_KEY_ID
    assert AWS_SECRET_ACCESS_KEY, AWS_SECRET_ACCESS_KEY
    logger.remove(0)
    logger.add(sys.stderr, level=log_level)
    logger.debug(infile)

    if dry:
        path, ext = os.path.splitext(processed_assignments)
        processed_assignments = f"{path}-dry{ext}"

    if not os.path.exists(processed_assignments):
        logger.warning(f"Processed Assignments file doesn't exist: `{processed_assignments}`.")
        with open(processed_assignments, "w+") as f:
            json.dump([], f)

    answer_fields = answer_fields.split(",")
    results: List[Dict] = [d for d in pd.read_csv(infile).fillna(0).to_dict(orient="records")]

    with open(processed_assignments, "r") as f:
        processed = json.load(f)

    processed_hits = set(p["assignment_id"] for p in processed)


    mturk = boto3.client(
        "mturk",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name="us-east-1",
        endpoint_url=endpoint_url,
    )
    output = defaultdict(dict)
    for r in tqdm(results):
        # if assignment id not processed already
        assignment_id = r["AssignmentId"]
        worker_id = r["WorkerId"]

        if assignment_id in processed_hits:
            continue

        ans = process_worker_answers(r, answer_fields)
        ans['worker'] = worker_id
        hit_id = r['HITId']
        
        p = output[hit_id]
        if not p:
            d = {c: r[f'Input.{c}'] for c in ("claim","verdict","text",'sources')}
            d['text'] = d['text'].replace('<br />', '\n')
            d['sources'] = d['sources'].split(', ')
            d['answers'] = []
            d['hit_id'] = hit_id
            output[hit_id] = d
            p = output[hit_id]

        p['answers'].append(ans)
            
        if not dry:
                mturk.approve_assignment(AssignmentId=r["AssignmentId"], RequesterFeedback="thank you!")
        else:
                logger.info(f"Accepting {assignment_id} from worker {worker_id}!")
        
        processed.append(
            {
                "assignment_id": assignment_id,
                "hit_id": hit_id,
                "worker_id": worker_id
            }
        )
    output = [v for _, v in output.items()]
    with open(outfile, "w+") as f:
        f.write('\n'.join(json.dumps(o) for o in output))



if __name__ == "__main__":
    main()
