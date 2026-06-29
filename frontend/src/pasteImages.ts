export function imageFilesFromClipboard(data: DataTransfer): File[] {
  const files: File[] = [];
  const seen = new Set<string>();
  const addFile = imageFileCollector(files, seen);

  for (const item of Array.from(data.items)) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (file) addFile(file);
  }

  for (const file of Array.from(data.files)) {
    addFile(file);
  }

  return collapseAlternateClipboardImageRepresentations(files);
}

export function clipboardHasText(data: DataTransfer): boolean {
  for (const type of Array.from(data.types)) {
    if (!type.startsWith("text/")) continue;
    if (data.getData(type).trim()) return true;
  }
  return false;
}

export function isPasteKeyboardShortcut(event: {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey?: boolean;
}): boolean {
  return (
    event.key.toLowerCase() === "v" &&
    (event.metaKey || event.ctrlKey) &&
    !event.altKey
  );
}

type ClipboardWithRead = Clipboard & {
  read?: () => Promise<ClipboardItem[]>;
};

export async function imageFilesFromSystemClipboard(): Promise<File[]> {
  const clipboard = navigator.clipboard as ClipboardWithRead | undefined;
  if (!clipboard?.read) return [];

  const items = await clipboard.read();
  const files: File[] = [];
  const seen = new Set<string>();
  const addFile = imageFileCollector(files, seen);

  for (const item of items) {
    const type = item.types.find((candidate) => imageExtensionForType(candidate));
    if (!type) continue;
    const blob = await item.getType(type);
    const ext = imageExtensionForType(blob.type || type) ?? ".png";
    addFile(new File([blob], `clipboard-image${ext}`, { type: blob.type || type }));
  }

  return files;
}

export function nextImageLabels(text: string, count: number): string[] {
  const matches = Array.from(text.matchAll(/\[Image\s+(\d+)\]/gi));
  const maxSeen = matches.reduce((max, match) => {
    const n = Number.parseInt(match[1] ?? "", 10);
    return Number.isFinite(n) ? Math.max(max, n) : max;
  }, 0);
  return Array.from({ length: count }, (_, index) => `[Image ${maxSeen + index + 1}]`);
}

export function appendWithSpacing(current: string, addition: string): string {
  if (!current.trim()) return addition;
  if (/\s$/.test(current)) return `${current}${addition}`;
  return `${current} ${addition}`;
}

export function replaceLastText(
  current: string,
  target: string,
  replacement: string,
): string {
  const index = current.lastIndexOf(target);
  if (index === -1) return appendWithSpacing(current, replacement);
  return `${current.slice(0, index)}${replacement}${current.slice(index + target.length)}`;
}

function imageFileCollector(files: File[], seen: Set<string>) {
  return (file: File) => {
    if (!looksLikeImage(file)) return;
    const key = `${file.name}:${file.type}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) return;
    seen.add(key);
    files.push(file);
  };
}

function collapseAlternateClipboardImageRepresentations(files: File[]): File[] {
  if (files.length <= 1) return files;
  if (!files.every(looksLikeImage)) return files;
  if (files.length > 3) return files;
  if (!files.every(hasGenericClipboardImageName)) return files;
  if (!looksLikeAlternateRepresentations(files)) return files;

  return [bestImageRepresentation(files)];
}

function hasGenericClipboardImageName(file: File): boolean {
  const name = file.name.trim();
  if (!name) return true;
  return /^(image|clipboard-image|pasted-image)(?:[-_\s]\d+)?(?:\.[a-z0-9]+)?$/i.test(
    name,
  );
}

function bestImageRepresentation(files: File[]): File {
  return [...files].sort((a, b) => imageTypeRank(a) - imageTypeRank(b))[0] ?? files[0];
}

function looksLikeAlternateRepresentations(files: File[]): boolean {
  const types = new Set(files.map(normalizedImageType).filter(Boolean));
  if (types.size > 1) return true;
  const sizes = new Set(files.map((file) => file.size));
  return sizes.size === 1;
}

function imageTypeRank(file: File): number {
  switch (normalizedImageType(file)) {
    case "image/png":
      return 0;
    case "image/jpeg":
    case "image/jpg":
      return 1;
    case "image/webp":
      return 2;
    case "image/gif":
      return 3;
    case "image/tiff":
    case "image/x-tiff":
      return 4;
    default:
      return 5;
  }
}

function looksLikeImage(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  return /\.(gif|jpe?g|png|tiff?|webp)$/i.test(file.name);
}

function imageExtensionForType(type: string): string | null {
  const normalized = normalizeImageType(type);
  switch (normalized) {
    case "image/gif":
      return ".gif";
    case "image/jpeg":
    case "image/jpg":
      return ".jpg";
    case "image/png":
      return ".png";
    case "image/tiff":
    case "image/x-tiff":
      return ".tiff";
    case "image/webp":
      return ".webp";
    default:
      return null;
  }
}

function normalizedImageType(file: File): string {
  const fromType = normalizeImageType(file.type);
  if (fromType) return fromType;
  const name = file.name.toLowerCase();
  if (name.endsWith(".png")) return "image/png";
  if (name.endsWith(".jpg") || name.endsWith(".jpeg")) return "image/jpeg";
  if (name.endsWith(".webp")) return "image/webp";
  if (name.endsWith(".gif")) return "image/gif";
  if (name.endsWith(".tif") || name.endsWith(".tiff")) return "image/tiff";
  return "";
}

function normalizeImageType(type: string): string {
  return type.split(";", 1)[0].trim().toLowerCase();
}

export function imageAttachmentNote(paths: string[]): string {
  if (paths.length === 1) return `Attached image: ${paths[0]}`;
  return ["Attached images:", ...paths.map((path) => `- ${path}`)].join("\n");
}

export function labeledImageAttachmentNote(labels: string[], paths: string[]): string {
  return labels.map((label, index) => `${label}: ${paths[index] ?? ""}`).join("\n");
}
