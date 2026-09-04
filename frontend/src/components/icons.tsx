/**
 * The icon set, inline.
 *
 * Drawn here rather than pulled from a package: the product needs eight
 * glyphs, and a dependency for eight glyphs is a dependency to audit, update
 * and ship for the rest of the project's life.
 *
 * Every icon is decorative -- each sits beside a text label or inside a
 * control that has an accessible name -- so all are `aria-hidden`. An icon
 * that ever becomes the only label needs a title, not a hidden attribute.
 */

interface IconProps {
  size?: number;
  className?: string;
}

function svg(path: React.ReactNode, { size = 16, className }: IconProps) {
  return (
    <svg
      className={`icon ${className ?? ''}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {path}
    </svg>
  );
}

export const PlusIcon = (props: IconProps) =>
  svg(
    <>
      <path d="M8 3.5v9" />
      <path d="M3.5 8h9" />
    </>,
    props,
  );

export const SendIcon = (props: IconProps) =>
  svg(<path d="M2 8l11.5-5-4 5 4 5L2 8z" />, props);

export const MenuIcon = (props: IconProps) =>
  svg(
    <>
      <path d="M2.5 4.5h11" />
      <path d="M2.5 8h11" />
      <path d="M2.5 11.5h11" />
    </>,
    props,
  );

export const DocumentIcon = (props: IconProps) =>
  svg(
    <>
      <path d="M9 1.5H4.5A1.5 1.5 0 003 3v10a1.5 1.5 0 001.5 1.5h7A1.5 1.5 0 0013 13V5.5L9 1.5z" />
      <path d="M9 1.5V5a.5.5 0 00.5.5H13" />
    </>,
    props,
  );

export const KeyIcon = (props: IconProps) =>
  svg(
    <>
      <circle cx="5" cy="8" r="2.5" />
      <path d="M7.5 8H14" />
      <path d="M11.5 8v2.5" />
    </>,
    props,
  );

export const UserIcon = (props: IconProps) =>
  svg(
    <>
      <circle cx="8" cy="5.5" r="2.5" />
      <path d="M3 13.5a5 5 0 0110 0" />
    </>,
    props,
  );

export const CloseIcon = (props: IconProps) =>
  svg(
    <>
      <path d="M4 4l8 8" />
      <path d="M12 4l-8 8" />
    </>,
    props,
  );

export const ChevronIcon = (props: IconProps) =>
  svg(<path d="M6 3.5L10.5 8 6 12.5" />, props);

/**
 * The product mark.
 *
 * Two bubbles -- a question asked and an answer returned -- rather than a
 * letter in a square, which is what a placeholder looks like. They are set
 * apart rather than overlapped: an overlap needs a knockout stroke in the
 * colour of whatever sits behind the mark, and this one sits on an accent
 * tile in the header and a tinted tile on the welcome screen. Separated
 * shapes read correctly on both, and still read at 16px.
 *
 * Drawn in a 24-unit box and scaled, so the geometry stays exact at any size.
 */
export function BrandMark({ size = 24 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      {/* The question: outlined, with a tail. */}
      <path
        d="M4.5 3.5h9a3 3 0 013 3v3.5a3 3 0 01-3 3h-4L6 16.5V13H4.5a3 3 0 01-3-3V6.5a3 3 0 013-3z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      {/* The answer: solid, because it is the thing the product returns. */}
      <rect x="12.5" y="14" width="10" height="7.5" rx="2.4" fill="currentColor" />
    </svg>
  );
}
