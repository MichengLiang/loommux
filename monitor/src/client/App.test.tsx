import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { App } from "./App";
import type { MonitorClientEvent } from "./events";

afterEach(() => {
	cleanup();
});

const baseEvent = {
	sequence: 1,
	received_at: 1_700_000_000_000,
	timestamp: 1_700_000_000,
};

function renderWithEvents(events: MonitorClientEvent[]) {
	return render(<App initialEvents={events} connect={false} />);
}

describe("monitor UI", () => {
	test("submitted, output, and finished events render focused code and output surfaces", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "execution_submitted",
				execution: 1,
				code: "print('hello')\n42",
				timeout_seconds: 5,
				workspace: "/workspace/demo",
				kernel_pid: 1234,
			},
			{
				...baseEvent,
				sequence: 2,
				type: "execution_output",
				execution: 1,
				stream: "stdout",
				text: "hello\n",
				kernel_execution_count: 7,
			},
			{
				...baseEvent,
				sequence: 3,
				type: "execution_output",
				execution: 1,
				stream: "result",
				text: "42\n",
				kernel_execution_count: 7,
			},
			{
				...baseEvent,
				sequence: 4,
				type: "execution_finished",
				execution: 1,
				status: "completed",
				output_total_lines: 2,
			},
		]);

		expect(screen.getAllByText("Execution 1").length).toBeGreaterThan(0);
		expect(screen.getAllByText("completed").length).toBeGreaterThan(0);
		expect(screen.getByText("print('hello')")).toBeTruthy();
		expect(screen.getAllByText(/hello/).length).toBeGreaterThan(0);
		expect(screen.getByText(/Out\[1\]: 42/)).toBeTruthy();
		expect(screen.getByText("Execution 1 | 2 lines")).toBeTruthy();
		expect(screen.getByLabelText("Python code")).toBeTruthy();
		expect(screen.getByLabelText("Python output")).toBeTruthy();
		expect(screen.queryByLabelText("Execution detail")).toBeNull();
	});

	test("error and traceback events render error and traceback", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "execution_submitted",
				execution: 2,
				code: "1 / 0",
			},
			{
				...baseEvent,
				sequence: 2,
				type: "execution_output",
				execution: 2,
				stream: "traceback",
				text: "ZeroDivisionError: division by zero",
			},
			{
				...baseEvent,
				sequence: 3,
				type: "execution_finished",
				execution: 2,
				status: "error",
				output_total_lines: 1,
				error: { ename: "ZeroDivisionError", evalue: "division by zero" },
			},
		]);

		expect(screen.getAllByText("Execution 2").length).toBeGreaterThan(0);
		expect(screen.getAllByText("error").length).toBeGreaterThan(0);
		expect(screen.getAllByText(/ZeroDivisionError/).length).toBeGreaterThan(0);
		expect(screen.getAllByText(/division by zero/).length).toBeGreaterThan(0);
	});

	test("tool timeline is omitted so code and output remain the primary surface", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "tool_call_started",
				call_id: "call-1",
				tool_name: "run_python",
				arguments: { code: "print('x')" },
			},
			{
				...baseEvent,
				sequence: 2,
				type: "tool_call_finished",
				call_id: "call-1",
				tool_name: "run_python",
				duration_ms: 12,
				ok: true,
				status: "completed",
				result_summary: "ok=true status=completed",
				pretty_text_summary: "Python execution completed.",
			},
		]);

		expect(screen.queryByLabelText("Tool timeline")).toBeNull();
		expect(screen.queryByText("Tool Timeline")).toBeNull();
	});

	test("execution sidebar can collapse", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "execution_submitted",
				execution: 3,
				code: "print('sidebar')",
			},
		]);

		expect(screen.getByText("Execution 3")).toBeTruthy();

		fireEvent.click(screen.getByRole("button", { name: /collapse execution list/i }));

		expect(screen.queryByText("Execution 3")).toBeNull();

		fireEvent.click(screen.getByRole("button", { name: /expand execution list/i }));

		expect(screen.getByText("Execution 3")).toBeTruthy();
	});

	test("code size control is available in the status bar", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "execution_submitted",
				execution: 4,
				code: "print('font')",
			},
		]);

		expect(screen.getByLabelText("Code and output font size")).toBeTruthy();
		expect(screen.getByText("Code 16px")).toBeTruthy();
	});

	test("clear view clears displayed events without implying backend deletion", () => {
		renderWithEvents([
			{
				...baseEvent,
				type: "execution_submitted",
				execution: 5,
				code: "print('clear')",
			},
		]);

		expect(screen.getAllByText("Execution 5").length).toBeGreaterThan(0);

		fireEvent.click(screen.getByRole("button", { name: /clear browser view/i }));

		expect(screen.queryByText("Execution 5")).toBeNull();
		expect(screen.getByText(/browser view only/i)).toBeTruthy();
	});
});
