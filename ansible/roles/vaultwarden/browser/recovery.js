// Recovery drill, as test(vaultwarden) #443 defined it: ?drill=create saves the canaries,
// ?drill=fresh checks a fresh install, the default verifies them, and &final=1 (the last
// verify) deletes the organization afterwards. &id= is the drill run's id (tools/lab-drill).
//   data           organization p2-canary-vaultwarden-<id> holding one login item of the
//                  same name, username p2-canary@example.invalid, in its default collection
//   configuration  the organization's Disable Send policy, enabled (a new one has none)
// Signs in as the lab-only owner in homelab-infra/apps/vaultwarden-drill-owner
// (owner_email, master_password). On create, an instance without that account registers
// it, which needs app.signups_allowed: true in the drill instance's config. Drill a lab
// instance, never the estate's credential store the runner reads.
// Vaultwarden stores only ciphertext, so the spec is a Bitwarden client of its own, in
// Node: it derives the keys from the master password, encrypts what it writes and
// decrypts what /api/sync returns, the way the official clients do.
const crypto = require('crypto');

module.exports = async ({ page, step, url, secrets }) => {
  const target = new URL(url);
  const requested = target.searchParams.get('drill') || 'verify';
  const mode = requested === 'fresh' ? 'absent' : requested;
  const final = target.searchParams.get('final') === '1';
  const base = target.origin;
  const canary = `p2-canary-vaultwarden-${target.searchParams.get('id') || 'drill'}`;
  const username = 'p2-canary@example.invalid';
  const DISABLE_SEND = 6;
  const email = String(secrets.owner_email).trim().toLowerCase();
  const password = String(secrets.master_password);
  const check = (value, message) => { if (!value) throw new Error(message); };

  // Bitwarden key derivation and type-2 (AES-256-CBC + HMAC-SHA256) cipher strings.
  const b64 = (buf) => Buffer.from(buf).toString('base64');
  const hkdfExpand = (prk, info) => crypto.createHmac('sha256', prk).update(Buffer.concat([Buffer.from(info), Buffer.from([1])])).digest();
  const split = (key) => ({ enc: key.subarray(0, 32), mac: key.subarray(32, 64) });
  const encrypt = (data, key) => {
    const iv = crypto.randomBytes(16);
    const cipher = crypto.createCipheriv('aes-256-cbc', key.enc, iv);
    const ct = Buffer.concat([cipher.update(Buffer.from(data)), cipher.final()]);
    const mac = crypto.createHmac('sha256', key.mac).update(Buffer.concat([iv, ct])).digest();
    return `2.${b64(iv)}|${b64(ct)}|${b64(mac)}`;
  };
  const decrypt = (text, key) => {
    const [type, body] = [text.slice(0, text.indexOf('.')), text.slice(text.indexOf('.') + 1)];
    check(type === '2', `unexpected cipher string type ${type}`);
    const [iv, ct, mac] = body.split('|').map(p => Buffer.from(p, 'base64'));
    const want = crypto.createHmac('sha256', key.mac).update(Buffer.concat([iv, ct])).digest();
    check(crypto.timingSafeEqual(want, mac), 'a cipher string failed its MAC check');
    const decipher = crypto.createDecipheriv('aes-256-cbc', key.enc, iv);
    return Buffer.concat([decipher.update(ct), decipher.final()]);
  };
  const rsaEncrypt = (data, publicKeyB64) => `4.${b64(crypto.publicEncrypt({
    key: crypto.createPublicKey({ key: Buffer.from(publicKeyB64, 'base64'), format: 'der', type: 'spki' }),
    padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: 'sha1' }, data))}`;
  const rsaDecrypt = (text, privateKeyDer) => {
    check(text.startsWith('4.'), `unexpected organization key type ${text.split('.')[0]}`);
    return crypto.privateDecrypt({
      key: crypto.createPrivateKey({ key: privateKeyDer, format: 'der', type: 'pkcs8' }),
      padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: 'sha1' }, Buffer.from(text.slice(2), 'base64'));
  };
  const keyPair = () => crypto.generateKeyPairSync('rsa', {
    modulusLength: 2048, publicKeyEncoding: { type: 'spki', format: 'der' }, privateKeyEncoding: { type: 'pkcs8', format: 'der' } });

  let kdfIterations = 600000;
  const masterKey = () => crypto.pbkdf2Sync(password, email, kdfIterations, 32, 'sha256');
  const passwordHash = (mk) => b64(crypto.pbkdf2Sync(mk, password, 1, 32, 'sha256'));
  const stretched = (mk) => ({ enc: hkdfExpand(mk, 'enc'), mac: hkdfExpand(mk, 'mac') });

  let token = '';
  const api = async (method, path, data) => {
    const response = await page.request.fetch(`${base}${path}`, {
      method, data, headers: token ? { Authorization: `Bearer ${token}` } : {} });
    check(response.ok(), `${method} ${path} returned ${response.status()}: ${(await response.text()).slice(0, 200)}`);
    const body = await response.text();
    try { return JSON.parse(body); } catch { return body || null; }
  };
  const signIn = async () => {
    const pre = await api('POST', '/identity/accounts/prelogin', { email });
    // An unknown account gets a decoy answer (often Argon2id); the drill owner is PBKDF2.
    if (pre.kdf !== 0) return false;
    kdfIterations = pre.kdfIterations;
    const response = await page.request.post(`${base}/identity/connect/token`, { form: {
      grant_type: 'password', username: email, password: passwordHash(masterKey()),
      scope: 'api offline_access', client_id: 'web', deviceType: '10',
      deviceIdentifier: 'b1d1d1d1-1ab0-4d1e-9d1e-1ab0d1d1d1d1', deviceName: 'lab-drill' } });
    if (!response.ok()) return false;
    token = (await response.json()).access_token;
    return true;
  };
  const vault = async () => {
    const sync = await api('GET', '/api/sync?excludeDomains=true');
    const userKey = split(decrypt(sync.profile.key, stretched(masterKey())));
    const privateKey = decrypt(sync.profile.privateKey, userKey);
    const orgs = sync.profile.organizations.filter(o => o.name === canary);
    const org = orgs[0];
    const orgKey = org ? split(rsaDecrypt(org.key, privateKey)) : null;
    const items = org ? sync.ciphers.filter(c => c.organizationId === org.id).map(c => ({
      name: decrypt(c.name, orgKey).toString(), username: c.login && c.login.username ? decrypt(c.login.username, orgKey).toString() : '' })) : [];
    const policies = sync.policies || [];
    return { orgs, org, orgKey, items, policies, userId: sync.profile.id };
  };

  if (mode === 'absent') {
    await step('a fresh install holds no canary', async () => {
      if (!(await signIn())) return;  // no owner account: nothing survived
      const { orgs } = await vault();
      check(orgs.length === 0, `the owner holds ${orgs.length} canary organization(s): data survived Remove`);
    });
    return;
  }

  await step('owner signs in (create registers the owner on an empty instance)', async () => {
    if (await signIn()) return;
    check(mode === 'create', 'the drill owner cannot sign in to this instance');
    const userKey = crypto.randomBytes(64);
    const pair = keyPair();
    // With mail off, Vaultwarden answers the verification request with the token itself.
    const verification = await api('POST', '/identity/accounts/register/send-verification-email',
      { email, name: 'Drill Owner', receiveMarketingEmails: false });
    check(typeof verification === 'string' && verification.length > 0,
      'Vaultwarden mailed a verification link instead of answering with a token; the drill instance needs mail off');
    kdfIterations = 600000;
    const mk = masterKey();
    const kdf = { kdfType: 0, iterations: kdfIterations, memory: null, parallelism: null };
    await api('POST', '/identity/accounts/register/finish', {
      email, emailVerificationToken: verification, name: 'Drill Owner', masterPasswordHint: null,
      masterPasswordAuthentication: { kdf, salt: email, hash: passwordHash(mk) },
      masterPasswordUnlock: { kdf, salt: email, masterKeyWrappedUserKey: encrypt(userKey, stretched(mk)) },
      userAsymmetricKeys: { publicKey: b64(pair.publicKey), encryptedPrivateKey: encrypt(pair.privateKey, split(userKey)) } });
    check(await signIn(), 'the drill owner was registered but cannot sign in');
  });

  if (mode === 'create') {
    await step('create the organization, its login item and its policy', async () => {
      let state = await vault();
      if (!state.org) {
        const sync = await api('GET', '/api/sync?excludeDomains=true');
        const orgKey = crypto.randomBytes(64);
        const pair = keyPair();
        await api('POST', '/api/organizations', {
          name: canary, billingEmail: email, planType: 0,
          key: rsaEncrypt(orgKey, sync.profile.publicKey || (await api('GET', '/api/accounts/keys')).publicKey),
          collectionName: encrypt('Default collection', split(orgKey)),
          keys: { publicKey: b64(pair.publicKey), encryptedPrivateKey: encrypt(pair.privateKey, split(orgKey)) } });
        state = await vault();
      }
      if (state.items.length === 0) {
        const collections = (await api('GET', `/api/organizations/${state.org.id}/collections`)).data;
        check(collections.length > 0, 'the canary organization has no collection');
        await api('POST', '/api/ciphers/create', {
          cipher: { type: 1, organizationId: state.org.id, encryptedFor: state.userId, name: encrypt(canary, state.orgKey), notes: null,
            favorite: false, reprompt: 0, login: { username: encrypt(username, state.orgKey), password: null, uris: null } },
          collectionIds: [collections[0].id] });
      }
      await api('PUT', `/api/organizations/${state.org.id}/policies/${DISABLE_SEND}`,
        { policy: { enabled: true, data: null } });
    });
  }

  await step('canaries exact, decrypted from the server', async () => {
    const { orgs, org, items, policies } = await vault();
    check(orgs.length === 1, `the owner holds ${orgs.length} canary organization(s)`);
    check(items.length === 1 && items[0].name === canary && items[0].username === username,
      `the organization holds ${JSON.stringify(items)}`);
    const policy = policies.find(p => p.organizationId === org.id && p.type === DISABLE_SEND);
    check(policy && policy.enabled, `the Disable Send policy is ${policy ? 'disabled' : 'absent'}`);
    if (final) {
      await api('DELETE', `/api/organizations/${org.id}`, { masterPasswordHash: passwordHash(masterKey()) });
      check((await vault()).orgs.length === 0, 'the canary organization is still listed after delete');
    }
  });
};
