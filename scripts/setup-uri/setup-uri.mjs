// Build a Self-hosted LiveSync Setup URI for one device.
//
//   node scripts/setup-uri/setup-uri.mjs <device> [--persistent]
//
// Reads (never from the command line, so nothing lands in shell history or
// a process list):
//   secrets/devices/<device>.key   the device's ES256 private key (device-key.sh)
//   SYNC_URL                       the public sync address, e.g. https://sync.example.com
//   E2E_PASSPHRASE                 LiveSync's end-to-end passphrase (same as the server's)
//   JWT_SUB, COUCHDB_DATABASE      as in your .env (defaults: owner, notes)
//   SETUP_URI_PASSPHRASE           optional; a random one is generated otherwise
//
// Prints the URI and its passphrase. Share them over two different channels:
// together they are the device's full credentials. By default the URI only
// opens until the end of the current 7-day window (LiveSync's "ephemeral"
// Setup URIs); --persistent makes one that never expires.
//
// The settings start from the library's DEFAULT_SETTINGS, the same defaults
// the sync daemon uses (document ids, path normalisation), not the plugin's
// "new vault" presets, which would not match an existing database.
import { readFileSync, statSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { DEFAULT_SETTINGS } from "@vrtmrz/livesync-commonlib/settings";
import { encodeTimeBoundSetupURI, decodeSettingsFromSetupURI } from "@vrtmrz/livesync-commonlib/setup-uri";

const die = (msg) => { console.error(`setup-uri: ${msg}`); process.exit(1); };
const [device, ...flags] = process.argv.slice(2);
if (!device || !/^[a-z0-9-]{1,32}$/.test(device)) die("usage: setup-uri.mjs <device> [--persistent]");
const persistent = flags.includes("--persistent");

const env = process.env;
const syncUrl = (env.SYNC_URL || "").replace(/\/+$/, "");
if (!/^https:\/\/[^/]+$/.test(syncUrl)) die("SYNC_URL must be https://<host> (devices only talk to the gate over TLS)");
const passphrase = env.E2E_PASSPHRASE || "";
if (passphrase.length < 16) die("E2E_PASSPHRASE is missing or shorter than 16 characters");
const database = env.COUCHDB_DATABASE || "notes";
const sub = env.JWT_SUB || "owner";

const keyFile = resolve(env.DEVICE_SECRETS_DIR || "secrets/devices", `${device}.key`);
let key;
try {
  if ((statSync(keyFile).mode & 0o077) !== 0) die(`${keyFile} is readable by others: chmod 600 it`);
  key = readFileSync(keyFile, "utf8").trim();
} catch (e) {
  if (e.code === "ENOENT") die(`${keyFile} not found: run scripts/device-key.sh ${device} first`);
  throw e;
}
if (!key.startsWith("-----BEGIN PRIVATE KEY-----")) die(`${keyFile} is not a PKCS#8 PEM private key`);

const settings = {
  ...DEFAULT_SETTINGS,
  couchDB_URI: syncUrl,
  couchDB_DBNAME: database,
  couchDB_USER: "",
  couchDB_PASSWORD: "",
  useJWT: true,
  jwtAlgorithm: "ES256",
  jwtKey: key,
  jwtKid: device,
  jwtSub: sub,
  jwtExpDuration: 5,
  encrypt: true,
  passphrase,
  usePathObfuscation: true,
  liveSync: true,
  syncOnStart: true,
  syncOnSave: true,
  isConfigured: true,
};

const uriPassphrase = env.SETUP_URI_PASSPHRASE || randomBytes(12).toString("base64url");
const result = await encodeTimeBoundSetupURI(settings, uriPassphrase, {
  mode: persistent ? "persistent" : "ephemeral",
  removeProperties: ["pluginSyncExtendedSetting"],
  skipDefaultValue: true,
});

// Round trip before printing: a URI the plugin can't open is worse than none.
const back = await decodeSettingsFromSetupURI(result.uri.trim(), uriPassphrase);
if (!back || back.jwtKid !== device || back.couchDB_URI !== syncUrl || back.passphrase !== passphrase) {
  die("the generated URI did not decode back to the same settings; not printing it");
}

const until = result.usableUntil ? new Date(result.usableUntil).toISOString() : "never (persistent)";
process.stdout.write(
  `Setup URI for ${device} (opens until ${until}):\n\n${result.uri}\n\n` +
  `Passphrase (send it another way than the URI):\n\n${uriPassphrase}\n\n` +
  "In Obsidian: install Self-hosted LiveSync, then 'Use a setup URI', paste, enter the passphrase.\n" +
  "When asked, FETCH from the server. Never overwrite the server from a new device.\n",
);
