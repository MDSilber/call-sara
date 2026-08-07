/** 'danny' -> 'Danny' — the display form of an owner key, for card
 * titles under an in-room owner filter. (The /api/owners menu carries
 * server-built labels; this covers the room's own selected key.) */
export function ownerTitle(owner: string): string {
  return owner.charAt(0).toUpperCase() + owner.slice(1)
}
