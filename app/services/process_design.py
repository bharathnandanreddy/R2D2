import google.generativeai as genai
from google.cloud import storage
import base64
import json
from collections import defaultdict
PROJECT_ID = "hacker2025-team-97-dev" 
model = genai.GenerativeModel("gemini-1.5-pro")



def consolidate_with_ai(requirement_text: str, evidences: list[dict]) -> dict:
    prompt = f"""
You are an aerospace systems engineer.

Below is a requirement followed by validation results from multiple design documents.
Each entry includes the design document name, status, and evidence summary.

Your task:
1. Analyze all the entries holistically.
2. Provide a **single consolidated status**: Fully Covered / Partially Covered / Not Covered.
3. Provide a brief explanation summarizing your final judgment using the evidence.

Respond in this JSON format:
{{
  "overall_status": "...",
  "consolidated_summary": "..."
}}

Requirement:
"{requirement_text}"

Validation Results:
{json.dumps(evidences, indent=2)}
"""

    try:
        response = model.generate_content(prompt)
        return json.loads(response.text[8:-4])
    except Exception as e:
        return {
            "overall_status": "Unable to Consolidate",
            "consolidated_summary": str(e)
        }

def validate_requirements(pdf_bytes: bytes, requirements: list[str], batch_size: int = 25) -> list[dict]:
    all_results = []

    for i in range(0, len(requirements), batch_size):
        batch = requirements[i:i + batch_size]

        prompt = f"""
You are an aerospace systems engineer.

Analyze the attached PDF design document and assess the coverage status for each of the following requirements. For each requirement, determine whether it is:
- Fully Covered
- Partially Covered
- Not Covered

Respond strictly in JSON array format like:
[
  {{
    "requirement": "<requirement text>",
    "status": "Fully Covered / Partially Covered / Not Covered",
    "evidence_summary": "Mention sections, figures, or content from the document that support your judgment."
  }},
  ...
]

Requirements:
{json.dumps(batch, indent=2)}
"""

        try:
            response = model.generate_content(
                contents=[
                    {"mime_type": "application/pdf", "data": pdf_bytes},
                    {"text": prompt}
                ]
            )
            parsed = json.loads(response.text[8:-4])
            all_results.extend(parsed)
        except Exception as e:
            for req in batch:
                all_results.append({
                    "requirement": req,
                    "status": "Parsing Failed",
                    "evidence_summary": str(e)
                })

    return all_results

def load_all_design_docs(bucket_name: str, folder: str = "response documents/") -> list[tuple[str, bytes]]:
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=folder)

    pdfs = []
    for blob in blobs:
        if blob.name.endswith(".pdf") or blob.name.endswith(".docx"):
            print(f"Loading design doc: {blob.name}")
            data = blob.download_as_bytes()
            pdfs.append((blob.name, data))
    return pdfs


def process_design(requirements: list[dict]):
    bucket_name = "hacker2025-team-97-dev.appspot.com"
    all_results = []

    design_docs = load_all_design_docs(bucket_name)
    
    for doc_name, pdf_bytes in design_docs:
        print(f"Processing validation against: {doc_name}")
        req_texts = [req["requirement"] for req in requirements]
        results = validate_requirements(pdf_bytes, req_texts)

        # Tag with metadata
        for i, item in enumerate(results):
            item["requirement_id"] = requirements[i]["requirement_id"]
            item["design_doc"] = doc_name

        all_results.extend(results)

    # Group by requirement_id
    grouped = defaultdict(list)
    for res in all_results:
        grouped[res["requirement_id"]].append({
            "design_doc": res["design_doc"],
            "status": res["status"],
            "evidence_summary": res["evidence_summary"]
        })

    # Consolidate per requirement using AI
    consolidated = []
    for req in requirements:
        req_id = req["requirement_id"]
        evidences = grouped.get(req_id, [])

        ai_summary = consolidate_with_ai(req["requirement"], evidences)

        consolidated.append({
            "requirement_id": req_id,
            "requirement": req["requirement"],
            "status": ai_summary.get("overall_status", "Unknown"),
            "evidence_summary": ai_summary.get("consolidated_summary", "No summary available")
        })

    return consolidated

    
