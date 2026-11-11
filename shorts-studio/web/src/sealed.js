// libsodium "sealed box" (crypto_box_seal), the encryption GitHub requires for Actions secrets.
// sealed = ephemeral_pk || crypto_box(message, nonce = BLAKE2b-24(ephemeral_pk || recipient_pk), recipient_pk, ephemeral_sk)
import nacl from './vendor/nacl-fast.cjs';
import blake from './vendor/blake2b.cjs';

export function seal(message, recipientPublicKey) {
  const ephemeral = nacl.box.keyPair.fromSecretKey(crypto.getRandomValues(new Uint8Array(32)));
  const input = new Uint8Array(64);
  input.set(ephemeral.publicKey, 0);
  input.set(recipientPublicKey, 32);
  const nonce = blake.blake2b(input, undefined, 24);
  const box = nacl.box(message, nonce, recipientPublicKey, ephemeral.secretKey);
  const sealed = new Uint8Array(32 + box.length);
  sealed.set(ephemeral.publicKey, 0);
  sealed.set(box, 32);
  ephemeral.secretKey.fill(0);
  return sealed;
}
