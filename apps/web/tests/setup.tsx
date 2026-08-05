import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => <div data-testid="echarts" data-option={JSON.stringify(option)} />,
}));
