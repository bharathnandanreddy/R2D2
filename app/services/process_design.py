import google.generativeai as genai
from google.cloud import storage
import json
from collections import defaultdict
import concurrent.futures
import re

PROJECT_ID = "hacker2025-team-97-dev"

genai.configure(api_key="AIzaSyDG8Azu4oGygUqs2-A_QECBDKIH9fSWkdg")

# Model for PDF validation (requires multimodal capabilities)
pdf_validation_model = genai.GenerativeModel("gemini-1.5-pro")

# Model for text-only consolidation (faster, more cost-efficient)
consolidation_llm = genai.GenerativeModel("gemini-2.0-flash") # Recommended

# --- Configuration for Parallelism ---
MAX_WORKERS_DOC_LOADING = 5
MAX_WORKERS_DOC_VALIDATION = 10
# MAX_WORKERS_CONSOLIDATION is no longer needed if we do a single batch consolidation call

# --- Helper for robust JSON parsing ---
def parse_ai_json_response(response_text: str) -> dict | list:
    """
    Parses JSON from AI response, handling markdown code block formatting.
    """
    match = re.search(r"```json\n(.*)\n```", response_text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = response_text

    try:
        data = json.loads(json_str)
        return data
    except json.JSONDecodeError as e:
        print(f"Warning: JSON decoding failed for AI response. Error: {e}. Raw text:\n{response_text[:500]}...")
        raise ValueError(f"Could not parse valid JSON from AI response: {e}")

# --- validate_requirements (uses pdf_validation_model) ---
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
            response = pdf_validation_model.generate_content( # <--- Uses pdf_validation_model
                contents=[
                    {"mime_type": "application/pdf", "data": pdf_bytes},
                    {"text": prompt}
                ]
            )
            parsed = parse_ai_json_response(response.text)
            all_results.extend(parsed)
        except Exception as e:
            print(f"Error validating a batch of requirements against document: {e}")
            for req in batch:
                all_results.append({
                    "requirement": req,
                    "status": "Parsing Failed",
                    "evidence_summary": str(e)
                })
    return all_results

# --- load_all_design_docs (remains the same) ---
def load_all_design_docs(bucket_name: str, folder: str = "response documents/") -> list[tuple[str, bytes]]:
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=folder))

    docs_to_download_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_DOC_LOADING) as executor:
        for blob in blobs:
            if blob.name.endswith(".pdf") or blob.name.endswith(".docx"):
                docs_to_download_futures.append(
                    executor.submit(lambda b: (b.name, b.download_as_bytes()), blob)
                )

        pdfs = []
        print(f"Loading {len(docs_to_download_futures)} design documents concurrently...")
        for future in concurrent.futures.as_completed(docs_to_download_futures):
            try:
                doc_name, data = future.result()
                pdfs.append((doc_name, data))
                print(f"Loaded: {doc_name}")
            except Exception as exc:
                errored_blob_name = None
                for orig_future in docs_to_download_futures:
                    if orig_future == future:
                        # Access original blob name from lambda
                        errored_blob_name = orig_future._args[0].name if orig_future._args else "unknown_blob"
                        break
                print(f"Error loading {errored_blob_name}: {exc}")
    return pdfs

# --- Helper Function for single document processing ---
def _process_single_doc(doc_tuple: tuple[str, bytes], requirements_info: list[dict], batch_size: int) -> list[dict]:
    doc_name, pdf_bytes = doc_tuple
    print(f"Starting validation against: {doc_name}")
    req_texts = [req["requirement"] for req in requirements_info]
    results = validate_requirements(pdf_bytes, req_texts, batch_size)

    processed_results = []
    for i, item in enumerate(results):
        item_copy = item.copy()
        item_copy["requirement_id"] = requirements_info[i]["requirement_id"]
        item_copy["design_doc"] = doc_name
        processed_results.append(item_copy)
    print(f"Finished validation against: {doc_name}")
    return processed_results

# --- Consolidated Consolidation Function (uses new consolidation_llm) ---
def consolidate_all_requirements_with_ai(all_requirements_and_evidences: list[dict]) -> list[dict]:
    """
    Consolidates results for all requirements in a single AI prompt using the dedicated consolidation LLM.
    """
    prompt = f"""

    You are an aerospace systems engineer.

    review multiple validation results for several system requirements and provide a single consolidated status and summary for *each* requirement.

    For each requirement entry includes the design document name, status, and evidence summary.
    
     analyze its validation results (evidences) from various design documents.

    Input JSON format (list of requirements, each with its evidences from specific documents):
    {json.dumps([{"requirement_id": "REQ-ID", "requirement_text": "Requirement text", "evidences": []}], indent=2)}
    Example:
    [
      {{
        "requirement_id": "REQ-001",
        "requirement_text": "The system shall maintain temperature below 25C.",
        "evidences": [
          {{
            "design_doc": "HVAC_Spec.pdf",
            "status": "Fully Covered",
            "evidence_summary": "Section 4.2 specifies cooling capacity to maintain 20C."
          }},
          {{
            "design_doc": "Thermal_Analysis.docx",
            "status": "Partially Covered",
            "evidence_summary": "Simulations show temperature reaches 28C in extreme conditions."
          }}
        ]
      }},
      // ... more requirements
    ]

    Your task:
    1.
    2. 
    3. 

    For each requirement:
    -  Analyze all the entries holistically.
    - Classify it as "functional" or "non-functional" based on its intent.
        - Functional: system behavior, configuration, switching logic, etc.
        - Non-functional: performance, latency, reliability, metrics, failover time, etc.
    - Analyze all evidence across design documents.
    - Provide a **single consolidated status**: Fully Covered / Partially Covered / Not Covered.
    - Provide a brief explanation summarizing your final judgment using the evidence.
    - Return:
        - **overall_status**: Fully Covered / Partially Covered / Not Covered
        - **consolidated_summary**: A 1–2 sentence explanation combining evidence.
        - **recommendation**: Suggest design/documentation changes for "Not Covered" or "Partially Covered"
        - **requirement_type**: Functional / Non-Functional

    Respond strictly in the following JSON array format. Ensure each object corresponds to a requirement from the input, identified by its 'requirement_id'.
    {json.dumps([{"requirement_id": "REQ-ID", "overall_status": "Status", "consolidated_summary": "Summary"}], indent=2)}
    Example:
    [
      {{
        "requirement_id": "REQ-001",
        "overall_status": "Partially Covered",
        "requirement_type": "Functional",
        "consolidated_summary": "The WAP Manager supports fallback switching, but LTE fallback is not tied to satellite handover.",
        "recommendation": "Update connectivity logic to support LTE fallback during satellite transitions, and document this in modem orchestration layer."

      }},
      // ... more consolidated results
    ]

    Requirements and their Validation Results:
    {json.dumps(all_requirements_and_evidences, indent=2)}
    """

    print(f"Calling consolidation LLM for {len(all_requirements_and_evidences)} requirements...")
    try:
        response = consolidation_llm.generate_content(prompt) # <--- Uses consolidation_llm
        parsed_results = parse_ai_json_response(response.text)
        if not isinstance(parsed_results, list):
            raise ValueError("AI did not return a JSON list as expected for consolidation.")
        return parsed_results
    except Exception as e:
        print(f"Error during consolidated AI consolidation: {e}")
        return [
            {
                "requirement_id": req_data["requirement_id"],
                "requirement": req_data["requirement_text"],
                "status": "Consolidation Failed",
                "evidence_summary": str(e)
            } for req_data in all_requirements_and_evidences
        ]

# --- Modified process_design ---
def process_design(requirements: list[dict]):
    bucket_name = "hacker2025-team-97-dev.appspot.com"
    all_intermediate_results = []

    # 1. Parallel Document Loading
    design_docs = load_all_design_docs(bucket_name)

    # 2. Parallel Document Validation against all requirements
    print("\nStarting parallel validation of requirements against design documents...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_DOC_VALIDATION) as executor:
        validation_futures = []
        for doc_tuple in design_docs:
            validation_futures.append(
                executor.submit(_process_single_doc, doc_tuple, requirements, 25)
            )

        for future in concurrent.futures.as_completed(validation_futures):
            doc_name_for_logging = None
            try:
                # Find the original doc_name for logging
                for doc_tuple, f_item in zip(design_docs, validation_futures):
                    if f_item == future:
                        doc_name_for_logging = doc_tuple[0]
                        break
                doc_results = future.result()
                all_intermediate_results.extend(doc_results)
            except Exception as exc:
                print(f"Error processing document {doc_name_for_logging or 'unknown'}: {exc}")

    # Group by requirement_id
    grouped_evidences = defaultdict(list)
    for res in all_intermediate_results:
        grouped_evidences[res["requirement_id"]].append({
            "design_doc": res["design_doc"],
            "status": res["status"],
            "evidence_summary": res["evidence_summary"]
        })

    # Prepare input for consolidated consolidation
    requirements_for_consolidation_prompt = []
    for req_data in requirements:
        req_id = req_data["requirement_id"]
        requirements_for_consolidation_prompt.append({
            "requirement_id": req_id,
            "requirement_text": req_data["requirement"],
            "evidences": grouped_evidences.get(req_id, [])
        })

    # 3. Single Consolidated Consolidation for all requirements
    if not requirements_for_consolidation_prompt:
        print("No requirements to consolidate.")
        return []

    final_consolidated_results = consolidate_all_requirements_with_ai(requirements_for_consolidation_prompt)

    # Reformat outputs for consistency
    formatted_results = []
    for item in final_consolidated_results:
        original_req_text = next((r["requirement"] for r in requirements if r["requirement_id"] == item.get("requirement_id")), "N/A - Original text not found")
        formatted_results.append({
            "requirement_id": item.get("requirement_id"),
            "requirement": original_req_text,
            "requirement_type": item.get("requirement_type", "N/A"),
            "status": item.get("overall_status", "Consolidation Error"),
            "recommendation": item.get("recommendation", "No recommendation provided by AI."),
            "evidence_summary": item.get("consolidated_summary", "Summary not provided by AI.")
        })

    return formatted_results