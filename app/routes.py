from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from app.services import requirement_store
from app.services.process_design import process_design
import google.cloud.storage as storage
from google.cloud import bigquery
import requests
from datetime import datetime
import csv
import io
from flask import Response


main = Blueprint("main", __name__)

PROJECT_ID = "hacker2025-team-97-dev" 
REGION = "us-central1"              
GCS_BUCKET_NAME = "hacker2025-team-97-dev.appspot.com" 

storage_client = storage.Client(project=PROJECT_ID)

@main.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@main.route("/requirements", methods=["GET"])
def requirements_page():
    return render_template("requirements.html")

@main.route("/api/requirements", methods=["GET"])
def get_requirements():
    return jsonify(requirement_store.get_requirements())

@main.route("/api/requirements/add", methods=["POST"])
def add_requirement():
    data = request.json
    new_req = {
        "requirement_id": f"REQ-{len(requirement_store.get_requirements())+1:04d}",
        "requirement": data["requirement"],
        "source_file": data["source_file"],
        "page_number": data["page_number"]
    }
    requirement_store.add_requirement(new_req)
    return jsonify({"status": "success"})

@main.route("/api/requirements/remove", methods=["POST"])
def remove_requirement():
    data = request.json
    requirement_store.remove_requirement(data["requirement_id"])
    return jsonify({"status": "removed"})

@main.route("/api/requirements/update", methods=["POST"])
def update_requirement():
    data = request.json
    req_id = data["requirement_id"]
    updated = {
        "requirement_id": req_id,
        "requirement": data["requirement"],
        "source_file": data["source_file"],
        "page_number": data["page_number"]
    }
    requirement_store.update_requirement(req_id, updated)
    return jsonify({"status": "updated"})

@main.route("/validate", methods=["GET"])
def validate_page():
    return render_template("validate.html")

@main.route("/api/validate", methods=["POST"])
def validate_api():
    requirements = requirement_store.get_requirements()  # Your local source
    if not requirements:
        return jsonify({"error": "No requirements to validate"}), 400

    # 1. Save requirements to BigQuery
    bq_client = bigquery.Client()
    table_id = "hacker2025-team-97-dev.requirements.extracted"
    
    for req in requirements:
        req["timestamp"] = datetime.utcnow().isoformat()  # optional

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )
    load_job = bq_client.load_table_from_json(requirements, table_id, job_config=job_config)
    load_job.result()

    print(f"Table {table_id} overwritten with {len(requirements)} rows")


    # 2. Call your Cloud Run validation endpoint
    VALIDATION_ENDPOINT = "https://validate-function-962417283534.europe-west1.run.app"  # Replace with actual Cloud Run URL
    try:
        print("calling validation endpoint")
        response = requests.post(VALIDATION_ENDPOINT , timeout=600)
        print("response received")
        response.raise_for_status()
        validation_results = response.json()
    except Exception as e:
        print(f"Error calling validation endpoint: {e}")
        return jsonify({"error": str(e)}), 500

    # 3. Return results to frontend
    return jsonify(validation_results)

@main.route("/docs", methods=["GET"])
def docs():
    return render_template("docs.html")

@main.route("/api/docs/upload", methods=["POST"])
def upload_doc():
    file = request.files["file"]
    doc_type = request.form["doc_type"]  # 'requirement' or 'design'
    folder = "source documents/" if doc_type == "requirement" else "response documents/"

    blob = storage_client.bucket(GCS_BUCKET_NAME).blob(folder + file.filename)
    blob.upload_from_file(file)
    if doc_type == "requirement":
        requirement_store.clear_requirements()
    return jsonify({"message": "Uploaded successfully"}), 200


@main.route("/api/docs/list", methods=["GET"])
def list_docs():
    requirement_docs = []
    design_docs = []
    blobs = storage_client.list_blobs(GCS_BUCKET_NAME)
    for blob in blobs:
        if blob.name.startswith("source documents/") and not blob.name.endswith("/"):
            requirement_docs.append(blob.name)
        elif blob.name.startswith("response documents/") and not blob.name.endswith("/"):
            design_docs.append(blob.name)
    return jsonify({
        "requirements": requirement_docs,
        "designs": design_docs
    })


@main.route("/api/docs/delete", methods=["POST"])
def delete_doc():
    blob_name = request.json["blob_name"]
    blob = storage_client.bucket(GCS_BUCKET_NAME).blob(blob_name)
    blob.delete()
    requirement_store.clear_requirements()
    return jsonify({"message": "Deleted successfully"}), 200


@main.route("/api/validation_report_csv", methods=["GET"])
def get_latest_validation_report_csv():
    bq_client = bigquery.Client()
    table_id = "hacker2025-team-97-dev.requirements.validation_report"

    query = f"""
      SELECT requirement_id, requirement, requirement_type, status, evidence_summary, recommendation, timestamp
      FROM `{table_id}`
      ORDER BY timestamp DESC
      LIMIT 100
    """

    try:
        query_job = bq_client.query(query)
        rows = list(query_job)

        # Create CSV in-memory
        output = io.StringIO()
        writer = csv.writer(output)
        # Write header
        writer.writerow(["requirement_id", "requirement", "requirement_type", "status", "evidence_summary", "recommendation", "timestamp"])
        # Write data rows
        for row in rows:
            writer.writerow([
                row.get("requirement_id", ""),
                row.get("requirement", ""),
                row.get("requirement_type", ""),
                row.get("status", ""),
                row.get("evidence_summary", ""),
                row.get("recommendation", ""),
                row.get("timestamp", "")
            ])

        output.seek(0)
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=validation_report_{datetime.utcnow().strftime('%Y-%m-%d-%H-%M-%S')}.csv"}
        )

    except Exception as e:
        return jsonify({"error": f"Failed to fetch report: {e}"}), 500