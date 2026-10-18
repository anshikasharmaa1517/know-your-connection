/** Anywhere in the app can send the person to the Friends tab. */
export function openFriends() {
  window.dispatchEvent(new Event('connections:friends'))
}
