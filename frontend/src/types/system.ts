/** Shape for GET /api/system/info, from docs/M0-CONTRACT.md. */

export interface SystemInfo {
  version: string;
  platform: string;
  python: string;
  app_data_dir: string;
  shared_dir: string;
  /** true when no shared folder was chosen and the app falls back to <app_data_dir>/shared */
  shared_dir_is_default: boolean;
  projects_dir: string;
  exports_dir: string;
}
