export type Repository = {
  id: string;
  owner: string;
  name: string;
  full_name: string;
  provider?: string;
  api_base_url?: string;
  clone_url?: string | null;
  local_path?: string | null;
  checkout_mode?: string;
  description?: string;
  default_branch?: string;
  last_sync_at?: string | null;
};

export type User = {
  id: string;
  name: string;
  email?: string | null;
  role: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string | null;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export type RepositoryMember = {
  id: string;
  repo_id: string;
  user_id: string;
  role: string;
  created_at: string;
  user_name?: string;
  user_email?: string | null;
};
