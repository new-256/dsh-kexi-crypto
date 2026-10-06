# DSH Plugin Format — Verified Research Report

> Read-only investigation of `C:\Users\lcl\AppData\Roaming\DSH Desktop\backend\dsh\`
> Purpose: port a CodeBuddy/WorkBuddy-style plugin (`.codebuddy-plugin/plugin.json` + `agents/*.md` + `skills/<name>/SKILL.md` + `avatars/*.png` + `settings.json` + `README.md`) into the DSH plugin format.
> Every claim below was verified by opening the cited file. Upstream source: `github.com/deepseek-ai/deepseek-harness`.

## 0. Layout warning

`...\backend\dsh\` is **not a source checkout**. It is an installed npm layout:

```
package.json  ->  {"name":"dsh-active-backend","dependencies":{"@deepseek-ai/dsh":"0.1.7-rc.1"}}
node_modules\@deepseek-ai\dsh-*\lib\*.js   (bundled ESM, dev-style, readable)
node_modules\@deepseek-ai\dsh-*\lib\types\*.d.ts
node_modules\@deepseek-ai\dsh-*\README.md / README.zh.md
```

There is **no `src/`, no `packages/`, no test directory, no fixtures** in the artifact (verified by recursive directory search). Package `repository.directory` fields point at upstream paths (`packages/bundle/web-app/`, `packages/preset/agent-preset/`, `packages/experimental/agent-team-profile/`).

Two additional authoritative sources used:

* **In-box skill** `@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/` — a complete guide to writing DSH plugins with working templates (`templates/decoration/`, `templates/mcp/`).
* **Live user data** `C:\Users\lcl\AppData\Roaming\DSH Desktop\dsh-home\` — real installed third-party plugins on this machine.

---

## 1. Manifest location & filename

**DSH has no `.dsh-plugin/plugin.json`, no root `plugin.json`, no `dsh-plugin.json`.**
Grep over the whole `@deepseek-ai` tree for `dsh-plugin.json|plugin\.json|\.dsh-plugin` → **0 matches**.

The manifest is **`package.json`**. A DSH "plugin" is one of two things:

### (a) A "bundle" = what CodeBuddy calls a plugin / expert package

Any npm package declaring `dsh.bundle.patch`.

`@deepseek-ai/dsh-package-manifest/lib/types/types.d.ts:66-74`
```ts
/** The configuration layer exported by a bundle package. */
export interface DshBundleManifest {
    /** One patch file path, or an ordered list applied in sequence, each relative to the declaring package root. */
    patch: string | string[];
}
/** The bundle composition declared by a profile directory. */
export interface DshProfileManifest {
    /** Ordered bundle layer list, using installed package names. */
    bundles?: string[];
}
```

`@deepseek-ai/dsh-app-boot/lib/types/profile.d.ts:1-14` (module doc, verbatim):

> "A profile is a directory under `$DSH_HOME/profiles/<name>` holding a `package.json` (out-of-tree plugin dependencies plus the profile manifest `dsh.profile` with its ordered `bundles` list) and a `cordis.patch.yml` (the user's own patch layer, applied after every bundle layer). Bundles are npm packages whose manifest declares `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }` (one file, or an ordered list of files); the tree is composed by applying each bundle's patch lists in `dsh.profile.bundles` order over an empty entry list, then the profile's own patches, then any launcher layers (`--patch` files and flag-derived patches)."

Bundle detection + enforcement, `dsh-app-boot/lib/index.js`:

```js:222
return manifest.dsh?.bundle?.patch === void 0 ? void 0 : manifest;
```
```js:496-497
const declared = typeof bundle.patch === "string" ? [bundle.patch] : bundle.patch;
if (!Array.isArray(declared) || !declared.every((file) => typeof file === "string")) throw new Error("dsh.bundle.patch must be a file path or a list of file paths");
```
```js:924
throw new Error(`${binName}: profile bundle ${JSON.stringify(packageName)} declares no dsh.bundle in its package.json`);
```

### (b) A plugin "row" inside a patch — the actual unit of loading

Every row is `{id, name, config, disabled}`. `name` is what Node resolves: an npm package name, a `file:` / `file:///` URL, or an absolute path. **There is no directory scanning of a plugins folder.**

**Key porting insight:** in DSH the "manifest" and "the plugin list" are the same object — a **Cordis YAML patch file**. Everything CodeBuddy expresses as JSON manifest fields, DSH expresses as YAML rows.

---

## 2. Manifest schema

`@deepseek-ai/dsh-package-manifest/lib/types/types.d.ts:1-90` (verbatim):

```ts
export interface DshPackageManifest {
    name: string;                 // published npm package name
    version: string;              // published npm package version
    description?: string;         // Package summary for discovery and display
    icon?: string;                // SVG, PNG, JPEG, or WebP file relative to this manifest's directory,
                                  // at most 256 KiB and contained there after realpath resolution
    private?: boolean;
    dependencies?: Record<string, string>;
    peerDependencies?: Record<string, string>;
    engines?: DshEnginesManifest; // { dsh?: string (semver range), node?, npm?, [k]: string|undefined }
    dsh?: DshManifest;
}
export interface DshManifest {
    manifestVersion?: 1;
    bundle?: DshBundleManifest;   // { patch: string | string[] }
    profile?: DshProfileManifest; // { bundles?: string[] }
    client?: DshClientManifest;
}
/** Literal text or translations indexed by lowercase language id, with a required English fallback. */
export type LocalizedText = string | { readonly en: string; readonly [locale: string]: string };
export interface PluginLocalizedMeta {
    title?: LocalizedText;
    description?: LocalizedText;
    icon?: string;                // Base64 image data URL read from the manifest's icon file
    error?: string;
}
export interface DshClientManifest {
    platform: string;             // Client platform identifier; the Web consumer selects `web`
    inject?: string[];            // informational package-name deps, not Cordis service injection
    immediately?: boolean;        // boot phase-one registration barrier
    external?: string[];          // exact module-table requests beyond the implicit client baseline
}
```

Note: **only `name` and `version` are required** in `DshPackageManifest`; everything else is optional.

### 2.0 Field mapping from CodeBuddy

| CodeBuddy field | DSH equivalent | Notes |
|---|---|---|
| `name` (id) | `package.json.name` **plus** row `id` in `cordis.patch.yml` | row `id` is the addressable key |
| `version` | `package.json.version` | |
| `description` | `package.json.description` | raw, untranslated |
| `author` | `package.json.author` (npm standard) | **no `dsh.author` field exists** |
| `displayName` | `locale/en.json` → `{"meta":{"title":"…"}}` (+ `locale/zh.json`) | §2.1 |
| `displayDescription` | same file, `meta.description` | §2.1 |
| `profession` | **not supported** — nearest: preset `description`, or persona prose (`dsh-persona.config.prefix`) | |
| `avatar` / `avatars/*.png` | top-level `package.json.icon` — **exactly one icon** | §2.2 |
| `categoryId` | **not supported** — nearest: `OPTIONAL_BUNDLES` membership, or preset `order` | |
| `tags` | **not supported** | |
| `agents[]` | preset row `config.plugins[]` (`@deepseek-ai/dsh-agent-preset`) | §2.3, §3 |
| `teamInfo` / `expertType` / `leadAgent` / `members` | **none of these exist** | §2.3 |
| `skills/<name>/SKILL.md` | **same convention exists natively** | §4 |
| `defaultInitPrompt` | **no manifest field** — nearest: `dsh-persona`, `plan-mode.config.section`, a skill body, or a client slot component | §2.4 |
| `quickPrompts` | **not supported** — nearest: slash `commands`, or a skill per prompt | §2.4 |
| `settings.json` | row `config:` (YAML), schema'd by the plugin's `Config` | §5, §8 |
| `hooks` | `@deepseek-ai/dsh-hooks-claude-code` row `{configPath, pluginRoot}` | real Claude-Code bridge |
| MCP servers | row `name: '@deepseek-ai/dsh-mcp-client'`, `config:{serverName, transport, url\|command, args, env, cwd, failOnStartupError}` | `templates/mcp/cordis.patch.yml` |
| tools | `@deepseek-ai/dsh-tool-*` rows, or `ctx.tools.register()` in your own `index.js` | §3 |
| commands | `ctx.commands.register({name, description, handler, input?})` | `dsh-commands/lib/types/index.d.ts:38-55` |

### 2.1 Multi-language display metadata

Display i18n is **not** an `{en, zh}` object in the manifest. It lives in `locale/<lang>.json` files, each containing a `meta` object.

Reader: `dsh-app-boot/lib/index.js:1906-1936`

```js
function dictionariesOf(englishPath, specifier, parentURL) {
	const dictionaries = new Map();
	for (const entry of readdirSync(dirname(englishPath), { withFileTypes: true })) {
		if (!entry.name.endsWith(".json")) continue;
		const resource = `${specifier}/locale/${entry.name}`;
		const language = entry.name.slice(0, -5);
		if (!LANGUAGE_ID.test(language)) throw new Error(`${resource} must use a language id as its filename`);
		const id = language.toLowerCase();
		if (dictionaries.has(id)) throw new Error(`${resource} duplicates locale ${id}`);
		const file = resolvePluginResource(resource, parentURL);
		if (dirname(file) !== dirname(englishPath)) throw new Error(`${resource}: ${file} must share the English locale directory ${dirname(englishPath)}`);
		const parsed = readObject(file);
		const meta = parsed.meta === void 0 ? void 0 : objectOf(parsed.meta, `${file}: meta`);
		dictionaries.set(id, {
			title: textOf(meta?.title, `${file}: meta.title`),
			description: textOf(meta?.description, `${file}: meta.description`)
		});
	}
	return dictionaries;
}
```

`LANGUAGE_ID = /^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$/u` (`:1831`).

Entry point `readPluginMeta(specifier, parentURL)` (`:1950-1980`), fallback chain documented at `:1937-1949`:
title → `locale/*.json` `meta.title` → `package.json.name` → full module specifier;
description → `meta.description` → `package.json.description` → `""`.

`@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/references/host-plugin.md:29-45`:

> Put the title and description in `locale/en.json` (other languages such as `locale/zh.json` use the same fields), and declare the icon as a top-level `icon` in `package.json`:
> ```json
> { "meta": { "title": "My Decoration", "description": "Draws a badge under the composer." } }
> ```
> ```json
> {
>   "icon": "./icon.svg",
>   "exports": { "./package.json": "./package.json", "./locale/*.json": "./locale/*.json" },
>   "files": ["locale/*.json", "icon.svg"]
> }
> ```
> Missing fields fall back to `package.json` `name` and `description` and to the panel's default artwork; malformed metadata produces a diagnostic and keeps the valid text.

⚠️ **You must add `"exports": { "./package.json": "./package.json", "./locale/*.json": "./locale/*.json" }`.** `readPluginMeta` resolves through the Node ESM resolver, which cannot see unexported files — without these subpath entries the localized name/description/icon silently vanish from the Plugins page.

Real installed example (`@deepseek-ai/dsh-experimental-agent-team-profile/locale/en.json`):
```json
{
  "meta": {
    "title": "Agent Teams",
    "description": "Enable team collaboration, team tools, the member roster, and the shared task board."
  }
}
```

### 2.2 Icon rules (verbatim, `dsh-app-boot/lib/index.js:1833-1857`)

```js
const MAX_ICON_BYTES = 256 * 1024;
const ICON_MEDIA_TYPES = new Map([
	[".svg", "image/svg+xml"], [".png", "image/png"], [".jpg", "image/jpeg"],
	[".jpeg", "image/jpeg"], [".webp", "image/webp"]
]);
function iconOf(value, manifestPath) {
	const icon = textOf(value, `${manifestPath}: icon`);
	if (icon === void 0) return void 0;
	if (isAbsolute(icon) || win32.isAbsolute(icon) || /^[A-Za-z][A-Za-z\d+.-]*:/u.test(icon)) throw new Error(`${manifestPath}: icon must be a relative file path`);
	const mediaType = ICON_MEDIA_TYPES.get(extname(icon).toLowerCase());
	if (mediaType === void 0) throw new Error(`${manifestPath}: icon must be SVG, PNG, JPEG, or WebP`);
	const directory = realpathSync(dirname(manifestPath));
	const file = realpathSync(resolve(directory, icon));
	const local = relative(directory, file);
	if (local === ".." || local.startsWith(`..${sep}`) || isAbsolute(local)) throw new Error(`${manifestPath}: icon must remain inside its manifest directory`);
	const stat = statSync(file);
	if (!stat.isFile()) throw new Error(`${manifestPath}: icon must be a regular file`);
	if (stat.size > MAX_ICON_BYTES) throw new Error(`${manifestPath}: icon exceeds 256 KiB`);
	const bytes = readFileSync(file);
	if (bytes.length > MAX_ICON_BYTES) throw new Error(`${manifestPath}: icon exceeds 256 KiB`);
	return `data:${mediaType};base64,${bytes.toString("base64")}`;
}
```

Consequences: **exactly one** icon per plugin; ≤256 KiB; format from the file **bytes**, so a `.png`-named SVG is an invalid PNG. **A folder of `avatars/*.png` → must collapse to one file.**

### 2.3 Teams / expert packages — the honest answer

**There is no declarative "team" or "expert package" concept.** `teamInfo`, `expertType`, `leadAgent`, `members` do not exist. Two unrelated mechanisms come closest.

**(i) Agent presets** — the only thing producing a *named, user-selectable "expert"*.

`@deepseek-ai/dsh-agent-preset-registry/lib/types/definition.d.ts:3-19`:
```ts
/** Identity, display fields and child Cordis plugins of one preset. */
export interface PresetDefinition {
    readonly id: string;
    readonly name?: string;
    readonly description?: string;
    readonly order?: number;
    readonly plugins: readonly (Omit<EntryOptions, 'id' | 'disabled'> & {
        id?: string;
        disabled?: EntryOptions['disabled'] | JsExpr;
    })[];
}
/** Validate a parsed Cordis entry list, including nested groups. */
export declare function entryListProblem(rows: unknown, at?: string): string | undefined;
```

Runtime schema, `@deepseek-ai/dsh-agent-preset/lib/index.js:13-19` (schemastery ≈ zod):
```js
static Config = z.object({
    id: z.string().required(),
    name: z.string(),
    description: z.string(),
    order: z.number(),
    plugins: z.array(z.any()).required()
});
```

Roster row a client reads, `dsh-agent-preset-registry/lib/types/types.d.ts:6-24`:
```ts
export interface AgentPresetRow {
    readonly id: string;
    /** Whether a session naming no preset composes this one. */
    readonly isDefault: boolean;
    readonly name?: string;
    readonly description?: string;
    /** Why this preset cannot compose a session; absent when it can. */
    readonly broken?: string;
}
export interface AgentPresetRoster {
    readonly presets: readonly AgentPresetRow[];
    readonly modeSelectionEnabled: boolean;
}
```

⚠️ `name` / `description` are **single plain strings, never `{en, zh}` objects** — `z.string()` makes an object a hard validation failure. Per `dsh-agent-preset-registry/lib/types/display.d.ts:29` ("A shipped preset publishes no `name`; a declaration that names itself owns its copy"), naming yourself also opts out of DSH's built-in dictionaries, so your text is never translated. **Bilingual `displayName` is not achievable at preset level.**

**(ii) Experimental Agent Teams** (`@deepseek-ai/dsh-experimental-agent-team`) — runtime, model-driven. Members are *spawned*, never declared.

`lib/types/types.d.ts:29-52`:
```ts
/** Durable teammate lifecycle. */
export type TeamMemberPhase = 'provisioning' | 'active' | 'failed';
/** Whole durable value written on every teammate lifecycle change. */
export interface TeamMemberSnapshot {
    readonly id: SessionId;
    readonly name: string;
    readonly description: string;
    readonly provider: string;
    readonly context: 'fresh' | 'fork';
    readonly phase: TeamMemberPhase;
    readonly error?: string;
}
/** Current runtime-enriched roster row. */
export interface TeamMemberView {
    readonly id: SessionId;
    readonly name: string;
    readonly role: 'lead' | 'teammate';
    readonly status: 'running' | 'inactive' | 'provisioning' | 'failed';
    readonly description?: string;
    readonly provider?: string;
    readonly context?: 'fresh' | 'fork';
    readonly model?: string;
    readonly diagnostics: string[];
}
```

`role: 'lead' | 'teammate'` is **runtime-derived**, not manifest-authored (Lead = the root Session, always `active` — `:84-86`). Team config is capacity only, from the bundle patch:

```yaml
- id: agent-team
  name: '@deepseek-ai/dsh-experimental-agent-team'
  config: { maxMembers: 8, maxTasks: 256, maxPendingMessagesPerMember: 64, maxMessageBytes: 65536, disposalTimeoutMs: 5000 }
- id: tool-agent-team
  name: '@deepseek-ai/dsh-experimental-tool-agent-team'
  config: { freshProvider: spawn, forkProvider: fork }
- id: ui-agent-team
  name: '@deepseek-ai/dsh-experimental-client-ui-agent-team'
```
(`dsh-experimental-agent-team-profile/cordis.patch.yml`, verbatim; it also *disables* `tool-subagent`, `tool-subagent-fork`, `tool-subagent-control`, `tool-subagent-list-agents`.)

`tool-agent-team` Config (`dsh-experimental-tool-agent-team/lib/types/index.d.ts:9-14`) has exactly two fields: `freshProvider?`, `forkProvider?`.

### 2.4 `defaultInitPrompt` / `quickPrompts`

**Neither exists in any DSH manifest field.** Grep for `quickPrompts|defaultInitPrompt|starterPrompt|examples:` over the whole tree → nothing (only unrelated `placeholder.*` conversation locales). Nearest equivalents:

1. `@deepseek-ai/dsh-persona` `config.prefix` (§3) — agent identity, not a prompt chip.
2. `@deepseek-ai/dsh-plan-mode` `config.section` — large verbatim guidance block (`presets/standard.patch.yml:50-60`).
3. `@deepseek-ai/dsh-tool-cordis` `config.section` — "extra guidance appended to the base tool description".
4. A **skill** — the closest functional and UX match: a skill is user-invocable (`/name`) by default and carries an instruction body. **Recommend porting `quickPrompts` as skills.**
5. Client-side `ctx.commands.register({name, description, handler})` — appears in the `/` menu with full control over the resulting prompt.

### 2.5 Two dead fields — do not copy

* `file-reader/package.json` → `"dsh": { "adapt": "0.1.2-alpha.5" }`
* `dsh-model-status/package.json` → `"dsh": { "compat": { "desktop": ">=0.3.4", ... } }`

Grep for `dsh\.adapt|dsh\.compat|manifest\?\.compat` across all of `@deepseek-ai` → **zero readers**. Third-party inventions, ignored by DSH. Use `engines.dsh` (`DshEnginesManifest`, documented as a SemVer range) or nothing.

---

## 3. Agent markdown format

**DSH does not parse agent-definition `.md` files.** There is no equivalent of `agents/*.md` with frontmatter. Grep for `allowed-tools|allowedTools` across the entire tree → **0 matches**. The only YAML-frontmatter parser in DSH is the skill loader (§4).

Agent behaviour is declared as **Cordis YAML rows**, each `{id, name, config, disabled}`:

* `id` — row id (`/^[a-z0-9][a-z0-9_-]*$/`), the address for overrides/disables; must be unique within a bundle.
* `name` — module specifier: npm name, `file:` URL, absolute path, or `cordis:group`.
* `config` — arbitrary YAML, validated against that plugin's own `Config` schema at mount time.
* `disabled` — boolean **or a `!!js` expression** (e.g. `disabled: !!js process.platform === 'win32'`).
* `group: true` + `isolate: {…}` — a row whose `config` is itself a row list (nested sub-tree).

### Frontmatter key comparison

| Claude-Code agent key | DSH |
|---|---|
| `name` | row `id` (kebab) + optional preset `name` (display) |
| `description` | preset `description` / row-level YAML comment |
| `tools` | `@deepseek-ai/dsh-tool-*` rows inside the preset's `plugins[]`; or `toolFilter: {allow, deny}` on a `tool-subagent` row |
| `model` | `config.agentOptions: {provider, model, reasoningEffort, maxTokens}` |
| system-prompt body | `@deepseek-ai/dsh-persona` row `config.prefix` / `config.suffix` |
| *(DSH-specific)* | `complete: true` (make prefix the entire system prompt), `includeRuntimeContext: false` |
| `avatar` | **no agent-level avatar** |
| lead / member role | **no declarative field** — runtime `TeamMemberView.role` (§2.3) |

### Persona row (the `systemPrompt` equivalent)

`@deepseek-ai/dsh-persona/lib/types/index.d.ts:23-40`:
```ts
/** Plugin config: the persona text this composition contributes. */
export interface Config {
    /** Persona prose rendered as the `deployment:persona-prefix` section. A template:
     *  complete `{{…}}` groups interpolate strictly against registered prompt variables.
     *  Empty text drops the section at render, matching the registry. */
    prefix: string;
    /** Persona suffix template rendered after first-party guidance. Omitted or empty
     *  text shadows the deployment suffix away; interpolation is strict. */
    suffix?: string;
    /** Make the prefix the complete system prompt, suppressing the suffix and every other section. */
    complete?: boolean;
    /** Suppress dynamic runtime-context snapshots for this persona's agent scope. */
    includeRuntimeContext?: boolean;
}
```

Module doc (`:1-14`) — important constraint:
> "`dsh-system-prompt` owns the global persona as its own config, and registers that section unconditionally — so this row is **scope-only**. Mounted inside an agent preset it shadows the deployment persona for that one session…; mounted globally it collides with the registry's own registration and fails loud."

### Subagent tool config

`@deepseek-ai/dsh-tool-subagent/lib/types/index.d.ts:16-73`:
```ts
/** Config: which registered provider this tool delegates to, plus child defaults. */
export interface Config {
    /** The `ctx.subagents` provider name to start runs on (e.g. `spawn`, `acp`). */
    provider: string;
    /** Model-facing tool name (default `subagent`). Each loaded instance must use a distinct name. */
    toolName?: string;
    modelSelectionSettings?: boolean;
    /** Expose `run_in_background` (default true). */
    enableRunInBackground?: boolean;
    backgroundMode?: 'one-shot' | 'continuable';
    /** Agent options applied to every child; omitted fields use child-loop defaults. */
    agentOptions?: AgentOptions;
    /** Per-child persona that shadows `deployment:persona-prefix`. */
    persona?: string;
    toolFilter?: { allow?: string[]; deny?: string[] };
    maxDepth?: number | 'provider-managed';
}
```

Runtime schema (`dsh-tool-subagent/lib/index.js:252-270`) agrees exactly.

**There is no `agents` field.** I verified this directly — a delegated agent asserted one existed; it was wrong. The way to get several differently-behaved subagents is to mount the plugin **multiple times** (one row per child profile) with distinct `toolName` values and a distinct `id` per row.

### No named catalogue of reusable subagent definitions

`dsh-subagent/lib/types/types.d.ts` `SubagentStartRequest` carries only `{label?, prompt: ContentBlock[], parent, signal, agentOptions?, outputSchema?, maxDepth?, toolFilter?, persona?}` — everything inline and per-call. **DSH has no `name → {description, prompt, tools, model}` catalogue that a model or user selects by name.** Nearest: (a) a whole preset; (b) `workflow`'s `agent(prompt, opts)` with `provider`/`model`/`label`/`phase`; (c) the per-child defaults on a `tool-subagent` row.

### `AGENTS.md` is read — but not as agent definitions

`@deepseek-ai/dsh-agent-instructions/lib/types/index.d.ts:2` — "Workspace instruction loader for AGENTS.md-compatible files." Loads `AGENTS.md` / `AGENTS.local.md` / `CLAUDE.md` / `CLAUDE.local.md` from the workspace as **raw plain text** injected into a `<system-reminder>`; **no frontmatter parsing**. 64 KiB cap (`maxBytes`, `presets/standard.patch.yml:19`).

---

## 4. Skill markdown format — the one 1:1 port

`@deepseek-ai/dsh-skill-filesystem` implements the Claude/CodeBuddy convention natively.

`dsh-skill-filesystem/README.md:36` (verbatim):
> "A skill is either a directory bundle `<name>/SKILL.md` or a flat file `<name>.md` at the top level of a scanned root; nested `**/SKILL.md` files are deliberately not discovered. The file starts with YAML frontmatter: required `name` and `description`, plus optional `whenToUse`, `metadata`, `disable-model-invocation`, and `user-invocable`."

### Honored frontmatter keys

`dsh-skill-filesystem/lib/index.js:664-705`:
```js
async function parseSkillFile(path, ctx, signal, trustedHost = false) {
	const raw = await readSkillText(ctx, path, signal, trustedHost);
	let parsed;
	try { parsed = parseFrontmatter(raw.content); }
	catch (error) { ctx.logger.warn(`skill file ${path} ignored: invalid YAML frontmatter: ${errorMessage(error)}`); return; }
	if (!parsed) { ctx.logger.warn(`skill file ${path} ignored: missing YAML frontmatter`); return; }
	const name = stringField(parsed.data, "name");
	const description = stringField(parsed.data, "description");
	if (name === void 0 || description === void 0) {
		ctx.logger.warn(`skill file ${path} ignored: frontmatter requires name and description`); return;
	}
	if (!isSkillName(name)) { ctx.logger.warn(`skill file ${path} ignored: invalid skill name "${name}"`); return; }
	let invocation;
	try { invocation = parseInvocationPolicy(parsed.data); }
	catch (error) { ctx.logger.warn(`skill file ${path} ignored: invalid invocation frontmatter: ${errorMessage(error)}`); return; }
	return { name, description,
		...optionalString(parsed.data, "whenToUse"),
		invocation,
		...optionalMetadata(parsed.data),
		path: raw.path, content: parsed.body.trim() };
}
```

Name grammar, `dsh-skill/lib/index.js:17`: `const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;` (enforced at `:685`, `:454`, `:466`, `:481`).

Invocation keys, `dsh-skill-filesystem/lib/index.js:848-877`:
```js
	rejectLegacyInvocationKey(data, "disableModelInvocation", "disable-model-invocation");
	rejectLegacyInvocationKey(data, "modelInvocable", "disable-model-invocation");
	rejectLegacyInvocationKey(data, "userInvocable", "user-invocable");
	const disableModelInvocation = frontmatterBoolean(data, "disable-model-invocation");
	const userInvocable = frontmatterBoolean(data, "user-invocable");
...
function rejectLegacyInvocationKey(data, legacy, canonical) {
	if (Object.hasOwn(data, legacy)) throw new Error(`frontmatter field "${legacy}" is unsupported; use "${canonical}"`);
}
```
Accepted boolean spellings (README `:38`): YAML booleans plus case-insensitive `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0`. **A rejected spelling or non-boolean value drops the whole skill with a warning** rather than silently permitting a surface.

| Key | Required | Notes |
|---|---|---|
| `name` | **yes** | kebab-case, `^[a-z0-9]+(?:-[a-z0-9]+)*$` |
| `description` | **yes** | short routing description |
| `whenToUse` | no | extra routing guidance |
| `metadata` | no | **object only** (`:879-881`); arrays/non-objects dropped |
| `disable-model-invocation` | no | true ⇒ out of model-facing catalogs/loaders |
| `user-invocable` | no | false ⇒ out of human-facing commands; omitted ⇒ permitted |
| `allowed-tools` | — | **NOT SUPPORTED** (0 matches tree-wide) |
| `model`, `tools`, `avatar`, `license` | — | **not supported**; unknown keys silently ignored |

**A Claude-Code skill with `allowed-tools` therefore loads fine — DSH simply ignores the tool grant.** CamelCase legacy keys (`userInvocable`, `disableModelInvocation`, `modelInvocable`) are the one case that errors, and it drops the whole skill.

Body = markdown after frontmatter → `SkillDefinition.content`, re-read from disk on every load (catalog and body have separate lifecycles — README `:42`).

### `scripts/` / `references/` / `templates/` — supported as a side effect

`discoverRoot` (`:586-608`) sets `locator = {path: <dir>/SKILL.md, directory: <dir>}` and `resourceBase: {kind: 'directory', path: locator.directory}`; only `SKILL.md` is treated specially. Sibling files/dirs stay reachable via `read`/`bash`.

**Proof — DSH's own plugin-development skill uses exactly this layout:**
```
dsh-agent-preset/skills/cordis-plugin-development/
├── SKILL.md
├── references/{host-plugin,ui-plugin,mcp-bundle,practices,verification}.md
└── templates/{decoration/{package.json,cordis.patch.yml,index.js,client.js}, mcp/{package.json,cordis.patch.yml}}
```

Only `SKILL.md` (or flat `<name>.md`) is a valid entry point — never `README.md`.

### Roots and priority (`lib/index.js:21-25, 84-85`; README `:46-56`)

| Rank | Source | Path |
|---|---|---|
| 100 | `project-dsh` | `<projectRoot>/.dsh/skills` |
| 200 | `project-agents` | `<projectRoot>/.agents/skills` |
| 300 | `custom` | `Config.customSkillDirs` |
| 400 | `user-dsh` | `<dshHome>/skills` |
| 500 | `user-agents` | `<agentsHome>/skills` (`$DSH_AGENTS_HOME` or `~/.agents`) |
| 600 | `bundled` | `Config.bundledSkillDir` ?? `$DSH_BUNDLED_SKILL_DIR` |

Lower rank wins name collisions (README `:63`). Project root = nearest ancestor containing `.git`, else cwd. `<dshHome>/skills` skips a `.system` child. Roots are watched (Chokidar; `fs.watchFile` at `watchPollIntervalMs` until attach) — **new/renamed/deleted skills need no restart**. `includeDefaultRoots: false` omits 100/200/400/500 and the env default.

Provider Config schema, `dsh-skill-filesystem/lib/index.js:31-44`:
```js
const Config = z.object({
	providerName: z.string().min(1).default("filesystem"),
	includeDefaultRoots: z.boolean().default(true),
	dshHome: z.string(),
	agentsHome: z.string(),
	customSkillDirs: z.array(z.string()).default([]),
	watch: z.boolean().default(true),
	watchUsePolling: z.boolean().default(false),
	watchStabilityThresholdMs: z.number().default(DEFAULT_WATCH_STABILITY_THRESHOLD_MS),
	watchPollIntervalMs: z.number().default(DEFAULT_WATCH_POLL_INTERVAL_MS),
	watchMaxProjects: z.number().default(DEFAULT_WATCH_MAX_PROJECTS),
	watchFollowSymlinks: z.boolean().default(true),
	bundledSkillDir: z.string()
});
```

⚠️ **Four gotchas for shipping skills inside a bundle:**

1. **There is no per-plugin skill directory.** The mechanism is a `customSkillDirs` entry computed with `!!js`. Canonical in-box example (`dsh-web-app/presets/cordis.patch.yml:143-147`, verbatim):
   ```yaml
   - id: skill-filesystem
     name: '@deepseek-ai/dsh-skill-filesystem'
     config:
       customSkillDirs:
         - !!js process.getBuiltinModule('node:path').join(process.getBuiltinModule('node:path').dirname(process.getBuiltinModule('node:module').createRequire(baseUrl).resolve('@deepseek-ai/dsh-agent-preset/package.json')), 'skills')
   ```
   Substitute your own package name; `skills/` must be in `files` and reachable by resolution.
2. The `standard` and `ptc` presets mount a bare `skill-filesystem` row with **no `config`** (`presets/standard.patch.yml:34-35`). To add your dir you must **override that row inside your preset's `plugins[]`**, restating `name` + full `config` (an override replaces the complete config).
3. **In Web the HOST-level `skill-filesystem` row is deliberately disabled** — `dsh-web-app/cordis.patch.yml:456-463`: "the base host `skill-filesystem` row is disabled here (presets own local skills)". So a skills-only bundle that only inserts a host-level row will not work on the `web` profile.
4. If you use `bundledSkillDir`, resolve it **statically at module scope into a local const** — `config.bundledSkillDir` is read once in the provider constructor (`:84-85`) and `resolve()`d; a per-cwd template needs its own provider.

---

## 5. Install / discovery path

**No plugins directory, no folder scan.** `dsh-home/plugins/` on this machine holds only a `DEV-DISCIPLINE.md`, source checkouts, and playwright user-data — grep for `plugins-store|"plugins"` finds only React panel ids. **It is not a load path.**

### Home & profile resolution

`@deepseek-ai/dsh-home-paths/lib/types/index.d.ts:7-11, 40-48`:
```ts
export declare const DSH_HOME_DIR_NAME = ".dsh";
export declare const DEFAULT_DSH_HOME_DISPLAY = "~/.dsh";
export declare const DSH_HOME_ENV = "DSH_HOME";
/** Precedence, highest first: an explicit configured path, $DSH_HOME, then ~/.dsh.
 *  An empty or whitespace-only $DSH_HOME is treated as unset. */
export declare function resolveDshHome(configured?: string, env?: Record<string, string | undefined>): string;
```

`dsh-app-boot/lib/index.js:516-519`:
```js
function resolveProfileDir(name, home = resolveDshHome()) {
	if (name === "" || name.includes("/") || name.includes("\\") || name === "." || name === ".." || name === "node_modules") throw new Error(`dsh: invalid profile name ${JSON.stringify(name)}`);
	return join(home, PROFILES_DIR, name);
}
```

`:521-527`:
```js
const PROFILE_TEMPLATES = {
	acp: { bundles: ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"] },
	web: { bundles: ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"] },
	headless: { bundles: ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"] },
	sdk: { bundles: ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-sdk-app"] },
	"sdk-minimal": { bundles: ["@deepseek-ai/dsh-sdk-minimal"] }
};
```
`:535` `const DEFAULT_PROFILE_BUNDLES = ["@deepseek-ai/dsh-base"];`
`:542` `const OPTIONAL_BUNDLES = ["@deepseek-ai/dsh-experimental-voice-input-bundle", "@deepseek-ai/dsh-experimental-agent-team-profile"];`

On this machine `DSH_HOME` = `C:\Users\lcl\AppData\Roaming\DSH Desktop\dsh-home` (`~/.dsh` does not exist).

A profile dir holds `package.json` (deps + `dsh.profile.bundles`), `cordis.patch.yml`, `node_modules/`, `pnpm-workspace.yaml`. Real example — `dsh-home/profiles/web/package.json`:
```json
{
  "name": "dsh-profile-web",
  "private": true,
  "dependencies": {
    "@captain1275/dsh-pet": "file:C:/Users/lcl/Desktop/DSH/plugins/captain1275-dsh-pet-0.3.3.tgz",
    "agy-first-bridge": "^1.7.1",
    "codebuddy-first-bridge": "file:C:/Users/lcl/Desktop/codebuddy-bridge",
    "dsh-chrome": "0.1.3",
    "dsh-comfyui-bridge": "^1.0.0",
    "dsh-dream-skin": "8.30.1",
    "workbuddy-auth": "file:C:/Users/lcl/Desktop/DSH/plugins/workbuddy-auth"
  },
  "dsh": { "profile": { "bundles": [ "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "web-search-panel", "…", "workbuddy-auth" ], "patchReload": "live" } }
}
```

### Patch layer precedence (verified by live row-id search)

| Layer | File | Priority |
|---|---|---|
| Home-level | `<home>/cordis.patch.yml` | lowest (seen by all profiles) |
| Per-profile bundles | `dsh.profile.bundles` order | middle |
| Per-profile user | `<home>/profiles/<p>/cordis.patch.yml` | above home + bundles |
| Launcher `--patch` | CLI, repeatable | highest |

Corroborated in `dsh-home/cordis.patch.yml:1-4` and `profile.d.ts:7-13`. **A per-profile patch cannot override a home-level insert** — put inserts at home level or in your own bundle.

### Three install routes

**(1) CLI — thin pnpm passthrough.** `@deepseek-ai/dsh/lib/bin.js:115`:
```js
const plugin = program.command("plugin").description("manage a profile's plugins by forwarding the remaining arguments to pnpm in the profile directory");
```
→ `dsh plugin --profile web add <spec>` / `remove` / `update`; specs are **pnpm's own**, including `file:` for local dev. Then flip it on via `dsh.profile.bundles` (or the Plugins UI). `dsh-app-boot/lib/types/profile-plugins.d.ts:52`: *"New bundle dependencies activate automatically."*

DSH-only subcommands (`dsh/lib/plugin-Dr5KNRuz.js:13-27`): `allow-version <pkg@version> --dsh-version <exact> --accept-risk`, `revoke-version`, `version-exemptions`.

**(2) Model-facing `plugin_manager` tool.** `dsh-plugin-manager/lib/types/tools.js:16-22`:
```js
action: { type: 'string', required: true, enum: ['list_plugins', 'list_bundles', 'set_plugin', 'set_bundle', 'install_bundle', 'remove_bundle', 'list_version_exemptions', 'set_version_exemption'], description: 'Management operation.' },
approvedBuilds: { type: 'array', items: { type: 'string' }, description: 'For install_bundle: pass names from pendingBuilds only after the user explicitly approves running their install scripts in the conversation. This grants persistent permission for this profile.' },
registry: { type: 'string', description: 'For install_bundle: the npm registry URL asked first, when the user names one; …' },
```
Actions are **snake_case**. Enabled in Creator mode; every action requires `danger-full-access` or per-call approval; an approval leaves the session's permission mode unchanged (`plugin-manager/README.md:31`).

**(3) Manual YAML edit** — add an `- insert:` row to a `cordis.patch.yml`. Hot-reloaded with `"patchReload": "live"`.

### Accepted install specs

`dsh-plugin-manager/lib/types/install-spec.d.ts:11-29`:
```ts
export type ParsedInstallSpec = {
    readonly kind: 'registry';  readonly spec: string; readonly name: string; readonly range?: string;
} | {
    readonly kind: 'path';      readonly spec: string; readonly path: string;
} | {
    readonly kind: 'tarball';   readonly spec: string; readonly path?: string; readonly host?: string;
} | {
    readonly kind: 'git';       readonly spec: string; readonly host: string;
};
```
`:40-48` doc: *"A path must be absolute: the Host's working directory means nothing to the person typing into a browser, and a relative path resolved against the profile would point inside it."* The CLI escapes this via `anchorPathSpec(argument, cwd)` (`operations.d.ts:38-43`). `:8-9`: a git spec and a tarball URL "carry the `host` pnpm fetches them from, which no registry stands in for; **only their dependencies come from the registry**".

### ⚠️ Local-dev loop caveat

Editing an already-installed `file:`/junction package changes **composition** but not the **module**:
`host-plugin.md:60` — *"Installing a new bundle can activate through HMR; **replacing an installed package requires restart to load a fresh JavaScript module generation**. Do not infer updated browser code from an unchanged slot id."*

The workaround actually used on this machine (`dsh-home/cordis.patch.yml:13-17`, verbatim):
```yaml
# 会话清理管理器：侧边栏任务的删除/回收站/恢复能力
- insert:
    - id: session-cleaner
      name: file:///C:/Users/lcl/AppData/Roaming/DSH%20Desktop/dsh-home/session-cleaner.plugin.mjs?v=9
      config:
        verbose: false
```
Header comment `:7`: *"?v=N 版本号用于绕过 ESM 模块缓存：修改 .mjs 源码后 bump 版本号即可热加载。"* — bump `?v=` for host edits; client-side edits need only a browser refresh (`:73`).

Another real form — bare package name resolved through a junction (`:46-54`):
```yaml
- insert:
    - id: bot-gateway
      name: dsh-bot-gateway
      config: { tasksRoot: '…', statePath: '…', settingsPath: '…' }
```
with comment `:56-59`: the browser half is automatic because `client-modules` scans `require.resolve('<pkg>/package.json')` for the `dsh.client` declaration — **one row = host + browser halves, no separate client row needed.**

---

## 6. Visualization

**No `views`/`panels`/`ui` manifest field exists.** No HTML-panel, iframe or webview affordance in a manifest. Three first-class mechanisms:

### (a) React slot injection — the primary custom-panel mechanism

API surface, `@deepseek-ai/dsh-client-ui-slots/lib/types/index.d.ts`:
```ts:83-85
export type SlotKind = 'single' | 'list' | 'keyed' | 'chain';
export type SlotScope = 'root' | 'session-maybe' | 'session';
```
```ts:789-804
register<K extends keyof SlotMap & string, …>(options: BaseOptions<K, EntryKey, D, H, M, N> & { inject?: undefined },
    component: C & SlotComponent<ComposedProps<…>> & RendersCheck<C, D>): () => void;
register<K extends keyof SlotMap & string, I extends object, …>(options: BaseOptions<…> & { inject: (...args: InjectParams<K, H>) => I },
    component: …): () => void;
```
`:769-775` — *"A second registration at an occupied cell's exact priority (default 0) **throws** naming the occupant."* `:153-156` — *"Declaring is claiming: the registering entry becomes the only entry…"*; registering into an **undeclared** slot throws.

**Real slot ids** (extracted from every `slots.register`/`slots.inject` call in the tree):
```
conversation.composer · conversation.composer.bar · conversation.composer.dock
conversation.input.left · conversation.input.right · conversation.input.dock · conversation.input.attachments
conversation.input.activity · conversation.input.model · conversation.input.overlay
conversation.input.permission · conversation.input.plan
conversation.header · conversation.header.leading · conversation.view · conversation.session
conversation.chat.node · conversation.chat.turnTail · conversation.chat.commandview
conversation.session.header{.actions,.corner,.lineage,.utilities}
conversation.hero.brand.mark · conversation.hero.workspace · conversation.hero.agentPreset
sidebar.right.tab.guide · sidebar.right.tab.guide.entry · sidebar.right.pane.tab{.title}
sidebar.right.tab.document{.action,.actions,.unpreviewable,.office.pdf} · sidebar.right.tab.menu.item
sidebar.workspaces{.session.row.action,.session.menu.item,.directoryFlow}
sidebar.brand.mark · sidebar.brand.name · sidebar.chat.conversation · sidebar.footer.action
sidebar.panellist · sidebar.settings · sidebar.toggle.badge
settings.plugins.tab · settings.section · settings.header · settings.action · settings.close
settings.general.item · settings.launcher · settings.onboarding · settings.trigger
plugins.item · plugins.bundle.config · plugins.bundle.activation · plugins.detail.section
plugins.detail.badge · plugins.detail.actions · plugins.row.config
tool.call.toolview · tool.view.cordis · deliverables.file.actions · shell.leading · shell.overlay · rightbar.session
```

**For a plugin-specific dashboard, the conventional home is `sidebar.right.tab.guide`** (`dsh-client-ui-sidebar-right/README.md:91,109`): it "accepts open-ended tab kinds", has a `guide` entry list where each entry has a provider-unique `id`, renders `sidebar.right.tab.guide.entry` with `{entry id, title, optional description, useTabInfo}`, and is opened via `openTab(kind, options?)`.

Working template — `dsh-agent-preset/skills/cordis-plugin-development/templates/decoration/client.js` (verbatim, 20 lines):
```js
window.__ModuleLoader__.load({
  id: '@local/my-decoration',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    function Decoration() {
      return h('svg', { viewBox: '0 0 64 64', width: 48, height: 48, 'aria-hidden': true,
        style: { display: 'block', pointerEvents: 'none' } }, h('circle', { cx: 32, cy: 32, r: 24, fill: '#247bbf' }));
    }
    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock', id: 'my-decoration', order: 5,
        }, Decoration));
      },
    };
  },
});
```

Rules: module id **must equal the bundle's npm `package.json.name`** (else `ERR_UNKNOWN_PLUGIN` — README:183); `require('react')` returns the **live** React so hooks work; `inject: ['slots']` is a load barrier; only the returned `{inject, apply}` matters; top-level side effects run at download; the factory is re-invoked on config-change rebuilds (make it idempotent).

Web-side config reaches your component **only** via the `plugins.*` slots' `settings`/`settingsPath` props (the client has no `ctx.get('config')`), plus `meta` (`{moduleId,name,version,description,homepage}`).

Registration is driven entirely by `dsh.client` + `exports["./client"]` — `dsh-client-modules/lib/index.js:711-728`:
```js
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
const decl = parseDshClient(packageName, dsh !== null && typeof dsh === "object" ? dsh.client : void 0);
if (decl === void 0 || decl.platform !== "web") { this.pkgMeta.set(sourceKey, null); return null; }
const clientRel = clientExportOf(packageName, pkg.exports);
if (clientRel === void 0) throw new Error(`client-modules: ${packageName} declares dsh.client but exports no "./client" bundle`);
```
`clientExportOf` (`:170-180`) accepts the **string** and **one-level conditional** forms. Serving URL: `:918` `const prefix = \`/plugins/${record.entry.id}/\`;` (also `:259` for sibling resources, `:293` for `client.js.map`). **One client half per row** (`ui-plugin.md:12`) → a dual-face package gives both halves in a single `id: my-expert` row.

Bonus (Web only): the browser boot fetches `/json/:profile?patch=true` and overlays it into the same profile-level patch `--patch` uses, so **web-side config is live without a Host restart**.

### (b) Host HTTP routes — a fully custom HTML dashboard

`@deepseek-ai/dsh-host-webserver/lib/types/index.d.ts:30-46` (verbatim):
```ts
/** Route match kind: 'exact' matches the pathname verbatim; 'prefix' p matches p and p/<anything>. */
export type WebRouteKind = 'exact' | 'prefix';
/** One named route registration. */
export interface WebRoute {
    kind: WebRouteKind;
    /** Absolute pathname, no trailing slash. */
    path: string;
    /** Owns the full response lifecycle (may hold the response open, e.g. SSE). */
    handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void>;
}
/** One exact-path HTTP upgrade registration. */
export interface WebUpgradeRoute {
    /** Absolute pathname, no trailing slash. */
    path: string;
    handler: (req: IncomingMessage, socket: Duplex, head: Buffer) => void | Promise<void>;
}
```
Service methods: `register(route)` (duplicate `(kind, path)` **throws** — `:84-90`), `registerUpgrade`, `registerFallback` (one owner only), `tapIndex`, `renderIndex`, `applyIndexTaps`, `collectIndexInjections`, `get port()`, `get host()`. Config `:47-59`: `host: '127.0.0.1' | '0.0.0.0'`, `port` (0 = OS-assigned), gzip options.

⚠️ **Authentication is NOT automatic.** `dsh-host-webserver/README.md:39` and `:113`:
> "`host` accepts exactly two values: `127.0.0.1` (default posture, loopback only) and `0.0.0.0` (deliberate network exposure — the server carries no TLS, authentication, or origin policy of its own)."
> "**No server-wide TLS, authentication, or origin policy** — route owners such as `dsh-client-connection` enforce their own request policy. Binding a non-loopback address still exposes unprotected routes and static assets to that network."

Real local precedent: `http://127.0.0.1:53035/bot-gateway/` — a full status board served by `dsh-bot-gateway` with its own `settings.json`.

### (c) `present` / deliverables — lowest effort, zero UI code

`@deepseek-ai/dsh-tool-present/lib/types/types.d.ts:3-19` (verbatim):
```ts
/** A declared filesystem file whose current contents remain at its source path. */
export interface PresentedFile {
    /** Original absolute path or path relative to the Session working directory. */
    path: string;
    /** Optional description supplied by the model. */
    description?: string;
}
declare module '@deepseek-ai/dsh-session/types' {
    interface SessionEventMap {
        /** Declared filesystem files from a successful final present result, including nested calls. */
        'deliverables/presented': { turn: number; callId: ToolCallId; files: PresentedFile[] };
    }
}
```
The Web "task deliverables" card lists them; `presented.preview` opens the **document-preview tab in the right sidebar**, whose registered viewers cover images, PDF, Office (docx/xlsx/pptx via `@deepseek-ai/dsh-office-to-pdf`), and markdown/text.

⚠️ `dsh-tool-present` is only mounted on some presets (`dsh-web-app/presets/cordis.patch.yml:150-151`: `- id: present` / `name: '@deepseek-ai/dsh-tool-present'`; absent from `standard.patch.yml`). Mount it in your preset's `plugins[]` if you need it.

### Not available

No `views`/`panels`/`ui` manifest field; no custom routing without a bundle; the Agent-Team UI is **not reusable** for arbitrary dashboards (it binds to `dsh-experimental-agent-team`'s `agentTeam` session projection: header trigger + roster panel + read-only task board).

⚠️ **Do not emit a raw clickable URL** into chat — it renders as plain text outside the frame. Only *attached images* render inline; a markdown link to a PNG does not display; URLs in tool output are never linkified.

---

## 7. Validation & sample fixtures

**No JSON Schema file, no test directory, no sample-manifest fixture exists in the installed artifact.** Verified by recursive directory search for `*test*` and `*fixture*` → 0 hits. (`/* v8 ignore */` annotations throughout prove coverage tests exist **upstream only**.)

Validation is **distributed across three layers**, which is why there's no single schema object:

1. **Bundle/profile reader** — hand-rolled throw-based JS: `dsh-app-boot/lib/index.js:497` (`dsh.bundle.patch must be a file path or a list of file paths`), `:924`, `:1844-1856` (`iconOf`), `:1906-1925` (`dictionariesOf`).
2. **Row config — schemastery (zod-like)** per plugin, at activation. Exposed as JSON Schema **without mounting**: `dsh --dump-config-schema` (`dsh/lib/bin.js:104`), and at runtime via `cordis_inspect_query` → `Config.listConfigs` (`host-plugin.md:54`: *"query `Config.listConfigs` for an installed plugin's schema before writing its `config`, and follow `$defs` references"*). Also `--dump-config`, `--dump-default-config`.
3. **Skill frontmatter & preset entry-list** — `parseInvocationPolicy` (hard-fail ⇒ drop skill), `entryListProblem(rows, at)` (`definition.d.ts:19`, returns the first invalid row).

### Full example fixture #1 — the canonical "expert package" (verbatim)

`@deepseek-ai/dsh-agent-preset/skills/editing-cordis-compositions/SKILL.md:20-50`:

`review-preset/package.json`
```json
{
  "name": "@local/dsh-review-preset",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```
`review-preset/cordis.patch.yml`
```yaml
- insert:
    - id: preset-review
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: review
        name: Review
        description: Reviews changes with the shell only.
        order: 10
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              prefix: You review software changes.
          - id: tool-bash
            name: '@deepseek-ai/dsh-tool-bash'
```
Same file `:14`: *"A declaration row has these `config` fields: `id` (required, lowercase letters, digits and hyphens), `plugins` (required Cordis entry list), and optional `name`, `description` and `order` (roster position). The Loader row `id` is `preset-<id>` by convention."*

`:18` — *"Write a bundle directory in the workspace with **exactly two files**, then install it."*
`:52` — *"Install with `plugin_manager`, `action: install_bundle`, `target` set to the absolute bundle directory."*
`:54-66` — changing a shipped preset = **override by Loader row id, not insert**; *"The override replaces the complete `config`, so restate `id`, `plugins` and every other field the shipped file carries."*
`:68-70` — **legacy preset migration**: *"Before declaration rows, a user preset was a directory `$DSH_HOME/.agent-presets/<id>/` holding `preset.yml` (display `name`, `description`, `order`) and `agent.cordis.yml` (the plugin entry list). **Nothing reads that directory any more.**"* (I confirmed those directories still exist on this machine and are dead.)

### Full example fixture #2 — Host-only bundle (verbatim)

`skills/cordis-plugin-development/references/host-plugin.md:9-27`:
```json
{
  "name": "@local/my-plugin",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "exports": { ".": "./index.js" },
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```
```yaml
- insert:
    - id: my-plugin
      name: '@local/my-plugin'
      config: {}
```
`:47-53` — export forms: *"do not mix"* —
* `export function apply(ctx, config) {}` + optional `export const inject = ['tools']` and `export const Config`.
* A service class as the default export.

### Full example fixture #3 — MCP-only bundle (configuration-only, 2 files)

`templates/mcp/package.json`:
```json
{
  "name": "@local/demo-mcp",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```
`templates/mcp/cordis.patch.yml`:
```yaml
- insert:
    - id: demo-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: demo
        transport: streamable-http
        url: http://127.0.0.1:3000/mcp
        failOnStartupError: true
```

### Full example fixture #4 — real published bundle (`dsh-experimental-agent-team-profile/package.json`, verbatim)

```json
{
  "name": "@deepseek-ai/dsh-experimental-agent-team-profile",
  "description": "Agent Teams collaboration, tools, and Web UI in one experimental bundle",
  "version": "0.1.7-rc.1",
  "icon": "./icon.svg",
  "type": "module",
  "main": "lib/index.js",
  "types": "lib/types/index.d.ts",
  "exports": {
    ".": { "types": "./lib/types/index.d.ts", "default": "./lib/index.js" },
    "./cordis.patch.yml": "./cordis.patch.yml",
    "./locale/*.json": "./locale/*.json",
    "./src/*": "./src/*",
    "./package.json": "./package.json"
  },
  "files": [ "icon.svg", "locale/*.json", "lib/index.js", "cordis.patch.yml", "lib/types/**/*.d.ts" ],
  "license": "MIT",
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } },
  "dependencies": { "…the three runtime packages…": ""
}
```
(`lib/index.js` is `export {};` — *"The package's runtime content is its `dsh.bundle.patch` document; this module exports no runtime API."*)

### Full example fixture #5 — real third-party dual-face plugin (`dsh-home/plugins/dsh-model-status/package.json`)

```json
{
  "name": "dsh-model-status",
  "description": "模型连通状态指示灯：…",
  "version": "0.6.0",
  "private": true,
  "type": "module",
  "main": "./lib/index.js",
  "exports": { ".": "./lib/index.js", "./client": "./lib/client.js", "./package.json": "./package.json" },
  "dsh": {
    "client": { "platform": "web",
      "inject": ["@deepseek-ai/dsh-client-runtime", "@deepseek-ai/dsh-client-ui-conversation", "@deepseek-ai/dsh-client-ui-model-selection"] },
    "compat": { "desktop": ">=0.3.4", "backend": ">=0.1.2-alpha.4", "verified": "DSH Desktop 0.3.4 / @deepseek-ai/dsh 0.1.2-alpha.4" }
  },
  "license": "MIT"
}
```
(`compat` is a third-party invention — ignored, see §2.5.)

---

## 8. Recommended port shape

```
dsh-<name>/
├── package.json          { name, version, private:true, type:"module",
│   │                       main:"./lib/index.js",
│   │                       exports:{ ".":"./lib/index.js", "./client":"./lib/client.js",
│   │                                 "./package.json":"./package.json",
│   │                                 "./locale/*.json":"./locale/*.json",
│   │                                 "./cordis.patch.yml":"./cordis.patch.yml" },
│   │                       files:["lib","skills","locale","icon.png","cordis.patch.yml"],
│   │                       icon:"./icon.png",            // from avatars/, ONE file, ≤256 KiB, real bytes
│   │                       engines:{ dsh:">=0.1.7" },    // NOT "compat" / "adapt"
│   │                       dsh:{ bundle:{ patch:"./cordis.patch.yml" },
│   │                             client:{ platform:"web", inject:[ "@deepseek-ai/dsh-client-ui-conversation" ] } } }
├── cordis.patch.yml      // ← REPLACES plugin.json ENTIRELY
│   ├── insert:
│   │     - id: my-expert                     # dual-face package: host + client halves in ONE row
│   │       name: '@local/dsh-my-expert'
│   │       config: { …settings defaults… }
│   ├── insert:
│   │     - id: preset-my-expert
│   │       name: '@deepseek-ai/dsh-agent-preset'
│   │       config:
│   │         id: my-expert
│   │         name: <display name — single string, no {en,zh}>
│   │         description: <one sentence>
│   │         order: 20
│   │         plugins:
│   │           - id: persona
│   │             name: '@deepseek-ai/dsh-persona'
│   │             config: { prefix: …, complete: true }        # ← from agents/*.md bodies
│   │           - id: skill-filesystem
│   │             name: '@deepseek-ai/dsh-skill-filesystem'
│   │             config:
│   │               customSkillDirs: [ !!js …resolve('@local/dsh-my-expert/package.json')…'skills' ]
│   │           - id: tool-skill
│   │             name: '@deepseek-ai/dsh-tool-skill'
│   │           - id: tool-fs / tool-pwsh / tool-subagent / present / …
│   └── (optional) insert: - id: my-mcp / name: '@deepseek-ai/dsh-mcp-client' / config: {…}
├── lib/index.js          export const inject=[…]; export const Config=z.object({…}); export function apply(ctx,config){…}
│                         //  ← also ctx.webServer.register({kind:'prefix', path:'/my-expert', handler}) for a dashboard
├── lib/client.js         window.__ModuleLoader__.load({ id:'@local/dsh-my-expert',
│                           factory(r){ return { inject:['slots'], apply(ctx){ ctx.slots.inject('sidebar.right.tab.guide', …) } } } })
├── skills/<kebab-name>/SKILL.md     // ← agents/*.md bodies + quickPrompts land here
│   └── (scripts/ references/ templates/ allowed via resourceBase)
├── locale/en.json        { "meta": { "title":…, "description":… } }
└── locale/zh.json        same shape
```

Mapping summary: `agents/*.md` → preset `plugins[]` rows (`dsh-persona` + tool rows); `settings.json` → row `config:` + a `Config` schema (the Settings page auto-generates a form — `SettingsDescriptor.autoGenerate`, `dsh-settings/lib/types/index.d.ts:8-19, 80-82`); `avatars/*.png` → one `icon`; `categoryId`/`tags`/`profession` → dropped into `description` or skill bodies.

**Two silent failure modes to design around:**
1. A skill dropped for a non-kebab `name` or a camelCase legacy key — check `logger.warn`, not the exit code.
2. Missing `exports["./locale/*.json"]` or an icon >256 KiB silently blanks the Plugins card.

---

## 9. Key file index

| Path (under `node_modules\@deepseek-ai\`) | What |
|---|---|
| `dsh-package-manifest\lib\types\types.d.ts` | **the manifest schema** (`DshPackageManifest`, `DshManifest`, `PluginLocalizedMeta`, `LocalizedText`, `DshClientManifest`) |
| `dsh-app-boot\lib\index.js` | `readPluginMeta` :1950 · `iconOf` :1841 · `dictionariesOf` :1906 · `resolveProfileDir` :516 · `PROFILE_TEMPLATES` :521 · `OPTIONAL_BUNDLES` :542 |
| `dsh-app-boot\lib\types\{profile,profile-plugins,package-meta,plugin-compatibility}.d.ts` | profile/bundle semantics |
| `dsh-agent-preset\skills\**` | **in-box plugin-development guide + templates + SKILL.md format** — the authoritative how-to |
| `dsh-skill-filesystem\lib\index.js` | skill discovery + frontmatter :664-705, :848-877, roots :21-25 |
| `dsh-skill\lib\{index.js,types/index.d.ts}` | `SKILL_NAME` regex :17 · `SkillSummary`/`SkillCandidate`/`SkillDefinition` |
| `dsh-web-app\cordis.patch.yml` + `presets\{standard,ptc,minimal,cordis}.patch.yml` | real preset/row/persona/tool/skill examples |
| `dsh-agent-preset-registry\lib\types\{definition,types,display}.d.ts` | `PresetDefinition`, `AgentPresetRow`, display rules |
| `dsh-persona\lib\types\index.d.ts` | `prefix`/`suffix`/`complete`/`includeRuntimeContext` |
| `dsh-tool-subagent\lib\types\index.d.ts` · `dsh-subagent\lib\types\types.d.ts` | subagent config + `SubagentStartRequest` |
| `dsh-experimental-agent-team\lib\types\*.d.ts` · `dsh-experimental-agent-team-profile\cordis.patch.yml` | runtime team members; team bundle |
| `dsh-client-ui-slots\lib\types\index.d.ts` | `register` overloads :789-804 · `SlotKind`/`SlotScope` |
| `dsh-client-modules\lib\index.js` | `dsh.client` scan :711-728 · `/plugins/<id>/` :259,918 |
| `dsh-host-webserver\lib\types\index.d.ts` + `README.md` | `WebRoute` :33-39 · no-auth note :39,113 |
| `dsh-tool-present\lib\types\types.d.ts` | `PresentedFile` |
| `dsh-plugin-manager\lib\types\{types,install-spec,operations,tools}.d.ts` + `README.md` | install specs, `BundleInfo`, patch precedence, build-script approval |
| `dsh-settings\lib\types\index.d.ts` | `SettingsDescriptor`, auto-generated forms |
| `dsh-hooks-claude-code\lib\types\config.d.ts` + `README.md` | Claude Code `hooks.json` bridge (`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`) |
| `dsh-home-paths\lib\types\index.d.ts` | `DSH_HOME_DIR_NAME=".dsh"`, `DSH_HOME_ENV` |
| `dsh\lib\bin.js:104-115` · `dsh\lib\plugin-Dr5KNRuz.js` | CLI: `--profile`, `plugin`, `allow-version`, `--dump-config-schema` |
