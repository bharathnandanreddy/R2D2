import functions_framework
from google.cloud import bigquery
from process_design import process_design 
from datetime import datetime

PROJECT_ID = "hacker2025-team-97-dev"
REQ_DATASET = "requirements"
REQ_TABLE = "extracted"
VALIDATION_TABLE = "validation_report"

@functions_framework.http
def hello_http(request):
    client = bigquery.Client(project=PROJECT_ID)

    # Step 1: Read requirements from BigQuery
    query = f"""
        SELECT requirement_id, requirement
        FROM `{PROJECT_ID}.{REQ_DATASET}.{REQ_TABLE}`
    """
    query_job = client.query(query)
    requirements = [dict(row) for row in query_job]

    if not requirements:
        return "No requirements found to validate.", 400

    # Step 2: Validate using your process_design function
    validation_results = process_design(requirements)

    # Step 3: Prepare rows with timestamp
    current_timestamp = datetime.utcnow().isoformat()
    rows_to_insert = []
    for result in validation_results:
        row = {
            "requirement_id": result.get("requirement_id"),
            "requirement": result.get("requirement"),
            "requirement_type": result.get("requirement_type"),
            "status": result.get("status"),
            "recommendation": result.get("recommendation"),
            "evidence_summary": result.get("evidence_summary"),
            "timestamp": current_timestamp
        }
        rows_to_insert.append(row)

    # Step 4: Overwrite table using load_table_from_json with WRITE_TRUNCATE
    table_id = f"{PROJECT_ID}.{REQ_DATASET}.{VALIDATION_TABLE}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    load_job = client.load_table_from_json(
        rows_to_insert,
        table_id,
        job_config=job_config,
    )
    load_job.result()  # Wait for job to complete


    return validation_results

