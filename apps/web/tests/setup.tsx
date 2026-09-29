import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => <div data-testid="echarts" data-option={JSON.stringify(option)} />,
}));

Object.defineProperty(window, "confirm", { value: vi.fn(() => true), writable: true });
Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true });
Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:test"), writable: true });
Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
Object.defineProperty(HTMLAnchorElement.prototype, "click", { value: vi.fn(), writable: true });
