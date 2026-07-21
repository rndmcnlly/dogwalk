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
  DAYTONA_API_BASE?: string;
  OPENAI_REALTIME_MODEL?: string;
  OPENAI_REALTIME_URL?: string;
  PUBLIC_ORIGIN?: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ADMIN_PASSWORD?: string;
}
