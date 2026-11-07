// Markdown is bundled as text (see the Text rule in wrangler.toml).
declare module "*.md" {
  const text: string;
  export default text;
}
