export interface Env {
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
  // The dogwalk-speaker Worker; absent in local dev and tests.
  SPEAKER?: SpeakerVerifier;
  // Private bucket of Voice Donation audio (voice_donation.ts); never holds operational audio.
  RESEARCH_AUDIO?: R2Bucket;
}

// RPC surface of the speaker verification Worker (speaker_worker/worker.js).
// Failures throw an Error whose message starts with a code: invalid-input,
// insufficient-audio, incompatible-reference, busy, or inference-failed.
export type SpeakerAudio = { audio: ArrayBuffer; format: "pcm_s16le"; sampleRate: number };
export type SpeakerReference = { embedding: number[]; embeddingSpace: string };
export interface SpeakerVerifier {
  embed(input: SpeakerAudio): Promise<SpeakerReference & { audioSeconds: number; speechSeconds: number }>;
  verify(input: SpeakerAudio & { reference: SpeakerReference }): Promise<{
    cosineSimilarity: number; embeddingSpace: string; audioSeconds: number; speechSeconds: number;
  }>;
}
