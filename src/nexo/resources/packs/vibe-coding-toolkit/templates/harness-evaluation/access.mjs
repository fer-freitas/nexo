// Deliberately insecure evaluation fixture; never copy into application code.
export function canReadRecord(actor, record) {
  return Boolean(actor && record && actor.id === record.ownerId);
}
