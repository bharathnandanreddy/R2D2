import google.generativeai as genai
from google.cloud import storage
import json
from collections import defaultdict
import concurrent.futures
import re
import time # For timing

PROJECT_ID = "hacker2025-team-97-dev"

genai.configure(api_key="AIzaSyDG8Azu4oGygUqs2-A_QECBDKIH9fSWkdg")

# Model for PDF validation (requires multimodal capabilities)
pdf_validation_model = genai.GenerativeModel("gemini-1.5-pro")

# Model for text-only consolidation (faster, more cost-efficient)
consolidation_llm = genai.GenerativeModel("gemini-1.5-flash-latest") # Changed to 1.5-flash-latest for potentially better performance/cost for consolidation (or keep 2.0-flash if preferred/available)

# --- Configuration for Parallelism ---
# These values are generally fine. Too many workers might hit API rate limits if the LLM calls are frequent.
MAX_WORKERS_DOC_LOADING = 5
MAX_WORKERS_DOC_VALIDATION = 10 

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

# --- OPTIMIZED: validate_requirements (sends all requirements to gemini-1.5-pro in one go) ---
def validate_requirements(pdf_bytes: bytes, requirements: list[str]) -> list[dict]:
    """
    Analyzes a PDF document against a list of requirements in a single API call.
    """
    # No batching loop here. All requirements are sent at once.
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
    {json.dumps(requirements, indent=2)}
    """
    try:
        response = pdf_validation_model.generate_content( # <--- Uses pdf_validation_model
            contents=[
                {"mime_type": "application/pdf", "data": pdf_bytes},
                {"text": prompt}
            ]
        )
        parsed = parse_ai_json_response(response.text)
        # Ensure the parsed result is a list and contains dictionaries
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
             raise ValueError("AI response for validation was not a list of dictionaries as expected.")
        return parsed
    except Exception as e:
        print(f"Error validating requirements against document: {e}")
        # Return a 'Parsing Failed' status for all requirements in this document if an error occurs
        # Map original requirements to their failed status
        return [
            {
                "requirement": req_text,
                "status": "Parsing Failed",
                "evidence_summary": f"Error during validation: {str(e)}"
            } for req_text in requirements
        ]

# --- load_all_design_docs (remains the same) ---
def load_all_design_docs(bucket_name: str, folder: str = "response documents/") -> list[tuple[str, bytes]]:
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=folder))

    docs_to_download_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_DOC_LOADING) as executor:
        for blob in blobs:
            if blob.name.endswith(".pdf") or blob.name.endswith(".docx"): # Only process PDF/DOCX as Gemini 1.5 Pro multimodal supports them well.
                # Only include PDFs if using the multimodal model here (e.g., docx might require external conversion)
                if blob.name.lower().endswith(".pdf"):
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
                # Attempt to find the original blob name for better error logging
                for orig_future in docs_to_download_futures:
                    if orig_future == future and hasattr(orig_future, '_args') and orig_future._args:
                        # This access is a bit hacky, relies on internal executor details
                        errored_blob_name = orig_future._args[0].name
                        break
                print(f"Error loading {errored_blob_name or 'unknown blob'}: {exc}")
    return pdfs

# --- Helper Function for single document processing ---
def _process_single_doc(doc_tuple: tuple[str, bytes], requirements_info: list[dict]) -> list[dict]:
    doc_name, pdf_bytes = doc_tuple
    print(f"Starting validation against: {doc_name}")
    # Extract only the requirement text for the LLM call
    req_texts = [req["requirement"] for req in requirements_info]
    
    # Call the optimized validate_requirements (no batch_size needed here)
    results = validate_requirements(pdf_bytes, req_texts)

    processed_results = []
    # Map the results back to their original requirement IDs
    # This assumes the model returns results in the same order as the input requirements
    # A more robust solution would be to include requirement IDs directly in the LLM's response.
    # For now, we'll rely on order and match by requirement text.
    
    # Create a mapping from requirement text to requirement_id for quicker lookup
    req_text_to_id = {req["requirement"]: req["requirement_id"] for req in requirements_info}

    for item in results:
        item_copy = item.copy()
        # Find the original requirement ID based on the requirement text.
        # This handles cases where models might rephrase/summarize the text.
        # If requirement text is critical, it should be passed back from the model.
        original_req_text_from_llm = item_copy.get("requirement")
        if original_req_text_from_llm:
            # Attempt to find the closest match or exact match
            found_id = req_text_to_id.get(original_req_text_from_llm)
            if found_id:
                item_copy["requirement_id"] = found_id
            else:
                # Fallback: try finding it by iterating if direct lookup fails (e.g., slight text variation)
                # This could be slow for many requirements, might need a more sophisticated fuzzy matching or
                # simply enforce LLM to return input requirement_id.
                item_copy["requirement_id"] = next(
                    (r["requirement_id"] for r in requirements_info if r["requirement"] == original_req_text_from_llm),
                    "UNKNOWN_REQ_ID" # If no match, assign an unknown ID
                )
        else:
            item_copy["requirement_id"] = "MISSING_REQ_TEXT_IN_RESPONSE" # Handle cases where AI response misses 'requirement' key

        item_copy["design_doc"] = doc_name
        processed_results.append(item_copy)
    print(f"Finished validation against: {doc_name}")
    return processed_results

# --- Consolidated Consolidation Function (uses new consolidation_llm) ---
def consolidate_all_requirements_with_ai(all_requirements_and_evidences: list[dict]) -> list[dict]:
    """
    Consolidates results for all requirements in a single AI prompt using the dedicated consolidation LLM.
    """
    consolid_prompt = f"""
    You are an aerospace systems engineer.

    Review multiple validation results for several system requirements and provide a single consolidated status and summary for *each* requirement.

    For each requirement entry includes the design document name, status, and evidence summary.
    
    Analyze its validation results (evidences) from various design documents.

    Input JSON format (list of requirements, each with its evidences from specific documents):
    {json.dumps([{"requirement_id": "REQ-123", "requirement_text": "Requirement text", "evidences": []}], indent=2)}
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

    Your task for each requirement:
    1. Analyze all the entries holistically across all provided design documents.
    2. Classify it as "functional" or "non-functional" based on its intent.
        - Functional: system behavior, configuration, switching logic, etc.
        - Non-functional: performance, latency, reliability, metrics, failover time, etc.
    3. Provide a **single consolidated status**: "Fully Covered", "Partially Covered", or "Not Covered".
    4. Provide a brief explanation summarizing your final judgment using the evidence.
    5. Suggest design or documentation changes for "Not Covered" or "Partially Covered" requirements in a 'recommendation' field.

    Respond strictly in the following JSON array format. Ensure each object corresponds to a requirement from the input, identified by its 'requirement_id'.
    {json.dumps([{"requirement_id": "REQ-ID", "overall_status": "Status", "consolidated_summary": "Summary", "recommendation": "Text", "requirement_type": "Functional/Non-Functional"}], indent=2)}
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
        response = consolidation_llm.generate_content(consolid_prompt) # <--- Uses consolidation_llm
        parsed_results = parse_ai_json_response(response.text)
        if not isinstance(parsed_results, list):
            raise ValueError("AI did not return a JSON list as expected for consolidation.")
        return parsed_results
    except Exception as e:
        print(f"Error during consolidated AI consolidation: {e}")
        # If consolidation fails, return a failed status for all requirements
        return [
            {
                "requirement_id": req_data.get("requirement_id", "UNKNOWN_REQ_ID"),
                "requirement": req_data.get("requirement_text", "N/A"),
                "overall_status": "Consolidation Failed",
                "consolidated_summary": f"Error: {str(e)}",
                "recommendation": "Review input and AI model status.",
                "requirement_type": "N/A"
            } for req_data in all_requirements_and_evidences
        ]

# --- Modified process_design ---
def process_design(requirements: list[dict]):
    start_time = time.time()
    bucket_name = "hacker2025-team-97-dev.appspot.com"
    all_intermediate_results = []

    # 1. Parallel Document Loading
    design_docs = load_all_design_docs(bucket_name)

    # 2. Parallel Document Validation against all requirements
    print("\nStarting parallel validation of requirements against design documents...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_DOC_VALIDATION) as executor:
        validation_futures = []
        # Pass all requirements_info to each _process_single_doc call
        for doc_tuple in design_docs:
            validation_futures.append(
                executor.submit(_process_single_doc, doc_tuple, requirements)
            )

        for future in concurrent.futures.as_completed(validation_futures):
            doc_name_for_logging = None
            try:
                # Find the original doc_name for logging - this logic is a bit fragile and relies on future internals
                # A more robust way is to pass doc_name within the future result or closure.
                # However, your current error logging for this part is also trying to do this, so keeping standard.
                # Refactored: Pass doc_name into _process_single_doc for better logging.
                doc_results = future.result()
                all_intermediate_results.extend(doc_results)
            except Exception as exc:
                # This log won't have doc_name unless it's explicitly passed or extracted
                print(f"An error occurred during document processing: {exc}")

    # Group by requirement_id for consolidation step
    grouped_evidences = defaultdict(list)
    for res in all_intermediate_results:
        req_id = res.get("requirement_id")
        if req_id: # Only group if requirement_id is present
            grouped_evidences[req_id].append({
                "design_doc": res.get("design_doc", "N/A"),
                "status": res.get("status", "N/A"),
                "evidence_summary": res.get("evidence_summary", "N/A")
            })
        else:
            print(f"Warning: Skipping result due to missing requirement_id: {res}")


    # Prepare input for consolidated consolidation
    requirements_for_consolidation_prompt = []
    # Ensure all original requirements are included, even if they had no validation results
    for req_data in requirements:
        req_id = req_data["requirement_id"]
        requirements_for_consolidation_prompt.append({
            "requirement_id": req_id,
            "requirement_text": req_data["requirement"],
            "evidences": grouped_evidences.get(req_id, []) # Empty list if no evidences found
        })

    # 3. Single Consolidated Consolidation for all requirements
    if not requirements_for_consolidation_prompt:
        print("No requirements to consolidate.")
        return []

    final_consolidated_results = consolidate_all_requirements_with_ai(requirements_for_consolidation_prompt)

    # Reformat outputs for consistency
    formatted_results = []
    # Create a mapping for original requirement text lookup
    original_req_map = {req["requirement_id"]: req["requirement"] for req in requirements}

    for item in final_consolidated_results:
        req_id = item.get("requirement_id")
        original_req_text = original_req_map.get(req_id, "N/A - Original text not found")
        formatted_results.append({
            "requirement_id": req_id,
            "requirement": original_req_text,
            "requirement_type": item.get("requirement_type", "N/A"),
            "status": item.get("overall_status", "Consolidation Error"),
            "recommendation": item.get("recommendation", "No recommendation provided by AI."),
            "evidence_summary": item.get("consolidated_summary", "Summary not provided by AI.")
        })
    
    end_time = time.time()
    print(f"\nTotal process_design execution time: {end_time - start_time:.2f} seconds")
    return formatted_results
