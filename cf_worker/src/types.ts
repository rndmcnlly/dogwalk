export interface Env {
  LOCAL_APP_TRIAL_ENABLED?: string;
  LOCAL_APP_TRIAL_ACP_URL?: string;
  LOCAL_APP_TRIAL_WORKSPACE?: string;
  APP_FRONTEND_ENABLED?: string;
  APP_FRONTEND_TOKEN_SHA256?: string;
  APP_FRONTEND_OWNER_PHONE?: string;
  DB: D1Database;
  VOICE_SESSIONS: DurableObjectNamespace;
  TERMINAL_RELAYS: DurableObjectNamespace;
  TWILIO_AUTH_TOKEN: string;
  TWILIO_API_BASE?: string;
  DAYTONA_API_KEY: string;
  DOGWALK_IDENTITY_SECRET: string;
  OPENAI_API_KEY: string;
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
  RECORDING_MUTE?: string;
  OPENAI_LIVE_API_BASE?: string;
  OPENAI_LIVE_ATTACH_BASE?: string;
  PUBLIC_ORIGIN?: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ADMIN_PASSWORD?: string;
  // Producer identity stamped on observations (OTel service.version, deployment.environment.name).
  CF_VERSION_METADATA?: WorkerVersionMetadata;
  DEPLOYMENT_ENVIRONMENT?: string;
}
