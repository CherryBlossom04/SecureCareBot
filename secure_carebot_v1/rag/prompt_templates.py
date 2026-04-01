SUMMARIZE_PROMPT_TEMPLATE = """
Task: Convert structured patient data into a Dense Semantic Summary for Search.
Rules:
- ALWAYS start with the Patient ID, Visit ID, and Date.
- Format: Use "Key: Value" pairs separated by pipes (|) or short markers.
- NO conversational filler (e.g., "The record indicates", "The patient has").
- NO inferences or unit conversions.
- Keep specific clinical terms (e.g., "HbA1c", "Hashimoto's", "PAD") isolated for better keyword indexing.

Example:
Header: [P00011 | V001 | 10-04-2025]
Data: {{"BP": "145/92", "Symptoms": ["Thirst", "Fatigue"], "Diagnosis": ["Type 2 Diabetes"]}}
Output: P00011 | V001 | 10-04-2025. Patient has BP of 145/92 with symptoms of thirst and fatigue, diagnosed with Type 2 Diabetes.

Execution:
Header: [{header}]
Data: {data}
Output:
"""


# ── Query decomposition ───────────────────────────────────────────────────────

SPLIT_QUERY_PROMPT_TEMPLATE = """### TASK
You are a query decomposition engine for a medical database.
Break the USER QUERY into exactly two or three search strings.

### SCHEMA
1. If a name is mentioned, the first search must be: "Personal profile and medical history of [Name]"
2. The second search must be: "Specific symptoms and clinical signs of [Name]"
3. The third search (if needed) must be: "Diagnosis and treatments for [Name]"

### RULES
- Output ONLY the search strings.
- Do NOT add numbers, bullet points, or "Here are the queries".
- One query per line.

### EXAMPLES
User: "What are John's symptoms?"
Personal profile and medical history of John
Specific symptoms and clinical signs of John

User: "Arun's diagnosis and meds"
Personal profile and medical history of Arun
Diagnosis, disease status, and medication for Arun

### USER QUERY
"{query}"

### OUTPUT
"""


# ── Name extraction ───────────────────────────────────────────────────────────

EXTRACT_NAME_PROMPT_TEMPLATE = """
You are a precision information extraction system.

Task:
Extract the full legal name of the patient from the query.

Rules:
- Return ONLY the names (First Name + Middle/Last Name if present).
- Strip possessive suffixes (e.g., "Arun Kumar's" becomes "Arun Kumar").
- If multiple names exist, separate them with a comma (e.g., Arun Kumar, Meenakshi Sundaram).
- DO NOT use Markdown, brackets [], quotes "", or the word "python".
- Extract ONLY the names. Do not include Visit IDs, or PatientIDs (e.g., P00011, V0000013), titles (Mr./Dr.), or possessive suffixes ('s).
- Capitalize each name properly.
- If no name is found, return an empty string.

Examples:
- "retrieve symptoms of Ammu" -> Ammu
- "Compare symptoms of Ammu and Priya" -> Ammu, Priya
- "Show Arun Kumar's and Meenakshi Sundaram's records" -> Arun Kumar, Meenakshi Sundaram
- "What are the sugar levels for Ravi, Balasubramanian, and Saravanan?" -> Ravi, Balasubramanian, Saravanan
- "Retrieve data for Visit V0000013" -> NONE

Query: {query}

Answer:
"""


# ── Chunk-type classification ─────────────────────────────────────────────────

EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE = """
You are a medical data classifier. Your task is to analyze a user query and map it
to one or more specific medical chunk categories for filtering.

Categories & Definitions:
- visit_symptoms: Current symptoms (e.g., pain, cough, nausea).
- visit_medication: Current medications/prescriptions.
- visit_overview: Current vitals (BP, sugar, weight, height, pulse, temperature).
- visit_blood_report: (HbA1c, Blood Sugar, Creatinine, Lipid Profile, LDL, HDL, Triglycerides, CBC, Hemoglobin, WBC, Platelets, TSH, T3, T4, Liver Function Test, LFT, SGOT, SGPT, Bilirubin, Vitamin D, Vitamin B12, Urea, Electrolytes, Sodium, Potassium, CRP, ESR).
- visit_scan_reports: (MRI Scan, CT Scan, X-Ray, Ultrasound, USG, Doppler Ultrasound, Echocardiogram, ECG, EKG, Retinal Imaging, Fundus Photography, FibroScan, RAIU Scan, CT Heart Calcium Score, Carotid Ultrasound, Renal CT Urogram, PET Scan, Bone Density, DXA Scan, Mammogram).
- history_symptoms: Past symptoms or symptoms from previous visits.
- history_prev_medication: Medications taken in the past.
- history_diagnosis: Past medical diagnoses.
- history_treatment: Records of previous treatments or procedures.
- history_overview: Past vitals (BP, sugar, weight, etc. from old visits).
- profile_risk: Allergies, hereditary diseases, or family history.

Instructions:
1. Analyze if the user is asking about the CURRENT visit or PAST history.
2. TEMPORAL RULE: If the query asks for "current," "now," or "today," ONLY return
   categories starting with 'visit_'. If the query asks for "past," "history,"
   "previous," or "last visit," ONLY return categories starting with 'history_'.
3. COMPARISON RULE: If the query asks for a "summary," "comparison," "change," or
   "trend," you MUST return BOTH the 'visit_' and 'history_' versions of that data type.
4. Return ONLY the category names as a comma-separated list.
5. Do not provide any conversational text or explanations.

Examples:
Query: "What are John's symptoms?"
Answer: visit_vitals

Query: "Show past medications and allergies"
Answer: history_prev_medication, profile_risk

Query: "Check his current BP and blood work"
Answer: visit_overview, visit_blood_report

Query: "How has his weight changed?"
Answer: visit_overview, history_overview

Query: "What is the specific diagnosis for Revathi?"
Answer: visit_scan_reports, history_diagnosis

Task:
Query: {query}
Answer:
"""

VALID_CHUNK_CATEGORIES: frozenset[str] = frozenset([
    "visit_symptoms",
    "visit_medication",
    "visit_overview",
    "visit_blood_report",
    "visit_scan_reports",
    "history_symptoms",
    "history_prev_medication",
    "history_diagnosis",
    "history_treatment",
    "history_overview",
    "profile_risk",
])

#
# RAG_CHAT_PROMPT_TEMPLATE = """
# You are a precision medical synthesis system.
#
# Task:
# 1. Provide a Natural Language Summary of the provided Context.
# 2. Provide clinical suggestions and medications if requested in the User Request: {query}
#
# Rules:
# - Paragraph 1: Synthesize all patient-specific facts (history, symptoms, current data) from the Context into a professional, flowing narrative.
# - Paragraph 2: Address any requests for suggestions, medications, or advice. Use professional medical insights based on clinical standards if the context does not contain them.
# - DO NOT use headers, labels, or bracketed titles (e.g., No "[Summary]" or "Medications:").
# - Separate the summary and the suggestions by exactly two newlines.
# - Return ONLY the synthesized response. No conversational filler.
# - Limit the total response length to: {length_limit}.
# - If the context is empty and no advice can be given, return "No medical records found."
# Context:
# {context}
#
# Answer:
# """

# RAG_CHAT_PROMPT_TEMPLATE = """
# You are Gemini, an authentic and adaptive medical AI collaborator. Your goal is to synthesize clinical data into clear, concise, and grounded insights.
#
# Task:
# 1. Synthesize the provided Context into a high-value clinical summary.
# 2. Provide medications, dosages, or treatment protocols ONLY if explicitly requested in the User Request: {query}
#
# Rules:
# - NO DISCLAIMERS: Do not explain why information is missing. If no medications are requested or found, leave Paragraph 2 entirely blank.
# - STYLE: Direct, professional, and dense. Use Markdown bolding for key clinical markers (e.g., **HbA1c 8.5%**, **non-healing ulcer**).
# - PARAGRAPH 1 (SYNTHESIS):
#     - For one patient: A grounded narrative of current status and history.
#     - For multiple: A comparative summary (e.g., "**P00011** presents X, while **P00013** presents Y").
# - PARAGRAPH 2 (TREATMENT):
#     - Provide specific suggestions (e.g., "**Metformin 500mg**") ONLY if the user specifically asked for treatments/meds.
#     - If not requested, DO NOT output any text for this paragraph.
# - PRIVACY: If PII is requested, return ONLY: "Permission not given."
# - FORMATTING: No headers, "In summary," or conversational fillers. Separate Paragraph 1 and 2 by exactly two newlines.
# - CONSTRAINT: Total response under {length_limit}. If context is empty, return "No medical records found."
#
# Context:
# {context}
#
# Answer:
# """

RAG_CHAT_PROMPT_TEMPLATE = """
You are a clinical AI assistant.

Understand the query: {query} and use the Context to answer.

Rules:
- Adapt to query:
  • Summary → give clinical overview
  • Comparison → highlight differences clearly
  • Specific query → give exact values
- Always provide brief clinical suggestions.
- Give medications/dosage ONLY if asked or present in context.
- Use **bold** for key values (e.g., **HbA1c 8.2%**, **BP 140/90**).
- No disclaimers or filler text.
- If multiple patients → clearly compare.
- If PII requested → "Permission not given."
- If no data → "No medical records found."

Format:
Answer:  
Suggestions:  

Context:
{context}

Answer:
"""