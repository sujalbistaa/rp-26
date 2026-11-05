export type Role = "OFFICER" | "ANALYST" | "ADMIN";
export type Band = "STRONG" | "POSSIBLE" | "WEAK";
export type ImageSource = "MUGSHOT" | "CCTV" | "PHONE" | "OTHER";
export type SearchStatus = "DETECTED" | "NO_FACE" | "COMPLETE";
export type RevealField = "address" | "phone" | "id_number";

export interface User {
  id: string;
  officer_id: string;
  name: string;
  role: Role;
  active: boolean;
  must_change_password: boolean;
}

export interface FaceQuality {
  face_px: number;
  blur: number;
  yaw: number;
  too_small: boolean;
  too_blurry: boolean;
  too_turned: boolean;
  low: boolean;
}

export interface FaceBox {
  index: number;
  box: [number, number, number, number];
  quality: FaceQuality;
}

export interface Probe {
  id: string;
  status: SearchStatus;
  width: number;
  height: number;
  faces: FaceBox[];
  gallery_size: number;
  case_number: string;
}

export interface MatchImage {
  image_id: string;
  confidence: number;
  band: Band;
  source: ImageSource;
  enrolled_at: string;
  position: number;
}

export interface MatchPerson {
  rank: number;
  person_id: string;
  record_id: string;
  band: Band;
  confidence: number;
  images_matched: number;
  images_total: number;
  images: MatchImage[];
}

export interface Results {
  id: string;
  status: SearchStatus;
  case_number: string;
  reason: string;
  officer: string;
  created_at: string;
  duration_ms: number | null;
  gallery_size: number | null;
  width: number;
  height: number;
  faces: FaceBox[];
  face_index: number | null;
  low_quality: boolean;
  attached_to_case: boolean;
  purged: boolean;
  calibration_version: number | null;
  calibration_synthetic: boolean;
  floor: number | null;
  people: MatchPerson[];
  notice: string;
}

export interface SearchRow {
  id: string;
  case_number: string;
  status: SearchStatus;
  created_at: string;
  people: number;
  low_quality: boolean;
  attached_to_case: boolean;
  purged: boolean;
}

export interface RecordRow {
  id: string;
  record_id: string;
  full_name: string;
  cover_image_id: string | null;
  images: number;
  cases: number;
  updated_at: string;
}

export interface RecordPage {
  total: number;
  items: RecordRow[];
}

export interface PersonCase {
  id: string;
  case_number: string;
  section: string | null;
  station: string | null;
  status: string;
  created_at: string;
}

export interface Note {
  id: string;
  body: string;
  author: string;
  created_at: string;
}

export interface RecordImage {
  id: string;
  source: ImageSource;
  enrolled_at: string;
  low_quality: boolean;
}

export interface Dossier {
  id: string;
  record_id: string;
  full_name: string;
  gender: string | null;
  birth_year: number | null;
  has_address: boolean;
  has_phone: boolean;
  has_id_number: boolean;
  created_at: string;
  updated_at: string;
  images: RecordImage[];
  cases: PersonCase[];
  notes: Note[];
}

export interface LogRow {
  at: string;
  officer: string;
  action: string;
  case_number: string | null;
  detail: { field?: string };
}

export interface ImageReport {
  filename: string;
  status: "ENROLLED" | "REJECTED" | "SKIPPED";
  reason: string | null;
  record_id: string | null;
  low_quality: boolean;
  face_px: number | null;
  blur: number | null;
  yaw: number | null;
  duplicates: { record_id: string; confidence: number }[];
}

export interface EnrollResult {
  record_id: string | null;
  person_id: string | null;
  report: ImageReport[];
}

export interface Batch {
  id: string;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
  total: number;
  processed: number;
  report: ImageReport[];
  error: string | null;
}

export interface AuditRow {
  id: number;
  at: string;
  actor_officer_id: string;
  actor_role: string | null;
  action: string;
  ip: string | null;
  case_number: string | null;
  probe_hash: string | null;
  results_returned: number | null;
  calibration_version: number | null;
  target_type: string | null;
  target_id: string | null;
}

export interface AuditPage {
  total: number;
  items: AuditRow[];
}

export interface Calibration {
  version: number;
  embedder_version: string;
  active: boolean;
  synthetic: boolean;
  n_genuine: number;
  n_impostor: number;
  floor: number;
  possible: number;
  strong: number;
  floor_calibrated: number;
  note: string | null;
  created_at: string;
}

export interface CreatedUser {
  user: User;
  temporary_password: string;
  totp_secret: string;
  totp_uri: string;
}
