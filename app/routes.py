from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from app.services import requirement_store
from app.services.process_design import process_design
import google.cloud.storage as storage
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
    requirements = requirement_store.get_requirements()
    validation_results = process_design(requirements)
    
    # Optional: combine back with IDs
    # for idx, result in enumerate(validation_results):
    #     result["requirement_id"] = requirements[idx]["requirement_id"]
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
