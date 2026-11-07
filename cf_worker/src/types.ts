export interface Env {
  DB: D1Database;
  VOICE_SESSIONS: DurableObjectNamespace;
  TWILIO_AUTH_TOKEN: string;
  DAYTONA_API_KEY: string;
  DOGWALK_IDENTITY_SECRET: string;
  OPENAI_API_KEY: string;
  TWILIO_ACCOUNT_SID: string;
  TWILIO_FROM_NUMBER: string;
  TWILIO_API_BASE?: string;
  DAYTONA_SNAPSHOT: string;
  DAYTONA_AGENT_SECRET?: string;
  DAYTONA_API_BASE?: string;
  OPENAI_PROJECT_ID: string;
  OPENAI_WEBHOOK_SECRET: string;
  OPENAI_LIVE_MODEL?: string;
  ORCHESTRATOR_MODEL?: string;
  ORCHESTRATOR_API_BASE?: string;
  ORCHESTRATOR_API_KEY?: string;
  ORCHESTRATOR_REASONING_EFFORT?: string;
  OPENAI_LIVE_API_BASE?: string;
  OPENAI_LIVE_ATTACH_BASE?: string;
  PUBLIC_ORIGIN?: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ADMIN_PASSWORD?: string;
}
