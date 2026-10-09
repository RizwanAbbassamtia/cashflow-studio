/** Shapes for /api/doctor, from docs/M0-CONTRACT.md. */

export type DoctorStatus = "ok" | "warn" | "fail";

export interface DoctorCheck {
  id: string;
  name: string;
  status: DoctorStatus;
  detail: string;
  fix_hint: string;
}

export interface DoctorReport {
  /** false when any check failed */
  ok: boolean;
  checks: DoctorCheck[];
}
