// Markdown is bundled as text (see the Text rule in wrangler.toml).
declare module "*.md" {
  const text: string;
  export default text;
}

// Raw PCM earcons are bundled as binary (see the Data rule in wrangler.toml).
declare module "*.pcm" {
  const data: ArrayBuffer;
  export default data;
}
