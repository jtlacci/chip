// CHIP's character: a round chip with a face. It breathes and blinks; with
// reduced motion it holds still.

export function avatar(size = 40) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("class", "avatar");
  svg.setAttribute("data-avatar", "");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "CHIP");
  svg.innerHTML = `
    <g class="avatar-body">
      <circle cx="32" cy="32" r="27" fill="#F7D774"/>
      <circle cx="32" cy="32" r="27" fill="none" stroke="#E2B73F" stroke-width="3" stroke-dasharray="6 7"/>
      <circle cx="32" cy="32" r="19" fill="#FBE7A1"/>
      <g class="avatar-eyes">
        <ellipse cx="25" cy="30" rx="2.6" ry="3.4" fill="#3A2E14"/>
        <ellipse cx="39" cy="30" rx="2.6" ry="3.4" fill="#3A2E14"/>
      </g>
      <path d="M26 38c3.4 3 8.6 3 12 0" fill="none" stroke="#3A2E14" stroke-width="2.4" stroke-linecap="round"/>
      <circle cx="21" cy="36" r="2.6" fill="#F2A07B" opacity=".7"/>
      <circle cx="43" cy="36" r="2.6" fill="#F2A07B" opacity=".7"/>
    </g>`;
  return svg;
}
