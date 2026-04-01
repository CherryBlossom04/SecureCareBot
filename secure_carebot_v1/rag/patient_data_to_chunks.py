from abc import ABC, abstractmethod

class IPatientDataToChunks(ABC):
    def __init__(self, patients: list[dict]):
        if not isinstance(patients, list):
            raise TypeError(f"Expected a list of patient dicts, got {type(patients).__name__}.")
        if not patients:
            raise ValueError("Patient list must not be empty.")
        self.patients = patients

    @abstractmethod
    def convert(self) -> dict[str, dict]:
        pass

def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def clean_data(data):
    if isinstance(data, dict):
        return {k: clean_data(v) for k, v in data.items() if not _is_empty(clean_data(v))}
    if isinstance(data, list):
        return [clean_data(item) for item in data if not _is_empty(clean_data(item))]
    return data


def add_chunk(chunks: dict, chunk_name: str, chunk_data: dict) -> None:
    cleaned = clean_data(chunk_data)
    useful_keys = [k for k in cleaned if k not in ("Patient ID", "Visit ID")]
    if useful_keys:
        chunks[chunk_name] = cleaned


# ── Qwen 1.5 / 2.5 implementation ────────────────────────────────────────────

class PatientDataToChunksForQwen2(IPatientDataToChunks):
    def __init__(self, patients: list[dict]):
        super().__init__(patients)

    def convert(self) -> dict[str, dict]:
        all_chunks: dict[str, dict] = {}

        for patient in self.patients:
            patient_id = patient.get("Patient ID")
            if not patient_id:
                print("Skipping patient record with missing 'Patient ID'.")
                continue

            chunks: dict = {}

            add_chunk(chunks, "profile_identity", {
                "Patient ID": patient_id,
                "Name":       patient.get("name"),
                "DOB":        patient.get("DOB"),
                "Address":    patient.get("Address"),
                "Phone":      patient.get("Phone"),
                "Email":      patient.get("Email"),
            })

            add_chunk(chunks, "profile_risk", {
                "Patient ID":          patient_id,
                "Allergies":           patient.get("Allergies"),
                "Hereditary Diseases": patient.get("Hereditary Diseases"),
            })

            for visit in patient.get("Clinical Data", []):
                visit_id = visit.get("Visit ID")
                if not visit_id:
                    print(f"Skipping clinical visit with missing 'Visit ID' for patient {patient_id}.")
                    continue

                add_chunk(chunks, f"visit_overview_{visit_id}", {
                    "Patient ID":  patient_id,
                    "Visit ID":    visit_id,
                    "Date":        visit.get("Date"),
                    "BP":          visit.get("BP"),
                    "Sugar Level": visit.get("Sugar Level"),
                    "Weight":      visit.get("Weight"),
                    "Height":      visit.get("Height"),
                    "Pulse":       visit.get("Pulse"),
                    "Temperature": visit.get("Temperature"),
                })

                add_chunk(chunks, f"visit_symptoms_{visit_id}", {
                    "Patient ID": patient_id,
                    "Visit ID":   visit_id,
                    "Symptoms":   visit.get("Symptoms"),
                })

                add_chunk(chunks, f"visit_medication_{visit_id}", {
                    "Patient ID":          patient_id,
                    "Visit ID":            visit_id,
                    "Current Medications": visit.get("Current Medications"),
                })

                add_chunk(chunks, f"visit_blood_report_{visit_id}", {
                    "Patient ID":   patient_id,
                    "Visit ID":     visit_id,
                    "Blood Report": visit.get("Blood Report"),
                })

                add_chunk(chunks, f"visit_scan_reports_{visit_id}", {
                    "Patient ID":   patient_id,
                    "Visit ID":     visit_id,
                    "Scan Reports": visit.get("Scan Reports"),
                })

            for history in patient.get("Patient History", []):
                visit_id = history.get("Visit ID")
                if not visit_id:
                    print(f"Skipping history record with missing 'Visit ID' for patient {patient_id}.")
                    continue

                add_chunk(chunks, f"history_overview_{visit_id}", {
                    "Patient ID":  patient_id,
                    "Visit ID":    visit_id,
                    "Date":        history.get("Date"),
                    "BP":          history.get("BP"),
                    "Sugar Level": history.get("Sugar Level"),
                    "Weight":      history.get("Weight"),
                    "Height":      history.get("Height"),
                    "Pulse":       history.get("Pulse"),
                    "Temperature": history.get("Temperature"),
                })

                add_chunk(chunks, f"history_symptoms_{visit_id}", {
                    "Patient ID": patient_id,
                    "Visit ID":   visit_id,
                    "Symptoms":   history.get("Symptoms"),
                })

                add_chunk(chunks, f"history_prev_medication_{visit_id}", {
                    "Patient ID":           patient_id,
                    "Visit ID":             visit_id,
                    "Previous Medications": history.get("Previous Medications"),
                })

                add_chunk(chunks, f"history_diagnosis_{visit_id}", {
                    "Patient ID": patient_id,
                    "Visit ID":   visit_id,
                    "Diagnosis":  history.get("Diagnosis"),
                })

                add_chunk(chunks, f"history_treatment_{visit_id}", {
                    "Patient ID":  patient_id,
                    "Visit ID":    visit_id,
                    "Medications": history.get("Medications"),
                    "Diet":        history.get("Diet"),
                })

            all_chunks[patient_id] = chunks

        return all_chunks
