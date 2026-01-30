import argparse
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from ollama import chat


MODEL = "hf.co/unsloth/Qwen3-1.7B-GGUF:UD-Q8_K_XL"


class AttentionFlag(BaseModel):
	flag_type: Literal["unresolved_action_item", "emerging_risk"]
	severity: Literal["low", "medium", "high"]
	title: str
	evidence: List[str] = Field(default_factory=list)
	owner: Optional[str] = None


class AnalysisResult(BaseModel):
	source_file: str
	project: Optional[str] = None
	summary: str
	flags: List[AttentionFlag] = Field(default_factory=list)


class PortfolioReport(BaseModel):
	total_threads: int
	total_flags: int
	high_severity_flags: int
	results: List[AnalysisResult]


SYSTEM_PROMPT = """
You are a strict portfolio health analyst for a Director of Engineering preparing a QBR.
Analyze the email thread and return ONLY JSON matching the provided schema.
Set the field "source_file" to the provided source file name.

Identify only these attention flags:
1) unresolved_action_item: questions, requests, or tasks that are still open or unanswered.
2) emerging_risk: risks, blockers, scope issues, dependency concerns, or missing confirmations.

Rules:
- If an issue is clearly resolved in the thread, do NOT flag it.
- Use severity based on potential impact: high (schedule/contractual risk), medium (scope/effort risk), low (minor clarification).
- Include 1-3 short evidence quotes pulled from the thread.
- If no flags exist, return an empty list.
- Keep summary to 1-2 sentences.
"""


def build_messages(thread_text: str, source_file: str) -> list:
	user_content = f"Source file: {source_file}\n\n{thread_text}"
	return [
		{"role": "system", "content": SYSTEM_PROMPT},
		{"role": "user", "content": user_content},
	]


def analyze_thread(thread_text: str, source_file: str, model: str, temperature: float) -> AnalysisResult:
	response = chat(
		model=model,
		messages=build_messages(thread_text, source_file),
		format=AnalysisResult.model_json_schema(),
		options={"temperature": temperature},
	)
	payload = response.message.content
	try:
		parsed = AnalysisResult.model_validate_json(payload)
	except Exception:
		return AnalysisResult(
			source_file=source_file,
			project=None,
			summary="Invalid LLM output: no flags extracted.",
			flags=[],
		)
	if not parsed.source_file:
		parsed.source_file = source_file
	return parsed


def load_threads(input_dir: Path, limit: Optional[int]) -> List[Path]:
	files = sorted(input_dir.glob("email*.txt"))
	if limit is not None:
		return files[:limit]
	return files


def build_report(results: List[AnalysisResult]) -> PortfolioReport:
	total_flags = sum(len(item.flags) for item in results)
	high_flags = sum(1 for item in results for flag in item.flags if flag.severity == "high")
	return PortfolioReport(
		total_threads=len(results),
		total_flags=total_flags,
		high_severity_flags=high_flags,
		results=results,
	)


def render_markdown(report: PortfolioReport) -> str:
	severity_order = {"high": 0, "medium": 1, "low": 2}
	all_flags = []
	for result in report.results:
		for flag in result.flags:
			all_flags.append((result, flag))
	all_flags.sort(key=lambda item: severity_order.get(item[1].severity, 3))
	lines = [
		"# Portfolio Health Report",
		"",
		"## Summary",
		f"- Threads analyzed: {report.total_threads}",
		f"- Total attention flags: {report.total_flags}",
		f"- High severity flags: {report.high_severity_flags}",
		"",
		"## Attention Flags",
	]
	if report.total_flags == 0:
		lines.append("- No attention flags detected.")
		lines.append("")
		return "\n".join(lines)

	current_severity = None
	for result, flag in all_flags:
		if flag.severity != current_severity:
			current_severity = flag.severity
			lines.append("")
			lines.append(f"### {current_severity.title()} Priority")
			lines.append("")
		lines.append(f"- **{flag.title}**")
		lines.append(f"  - Type: {flag.flag_type}")
		lines.append(f"  - Source: {result.source_file}")
		lines.append(f"  - Summary: {result.summary}")
		if flag.owner:
			lines.append(f"  - Owner: {flag.owner}")
		lines.append("  - Evidence:")
		for evidence in flag.evidence:
			lines.append(f"    - {evidence}")
	lines.append("")
	return "\n".join(lines)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Generate a portfolio health report from email threads.")
	parser.add_argument(
		"--input-dir",
		type=Path,
		default=Path(__file__).with_name("AI_Developer"),
		help="Directory containing email*.txt files.",
	)
	parser.add_argument(
		"--output-json",
		type=Path,
		default=Path(__file__).with_name("portfolio_health_report.json"),
		help="Path for JSON output.",
	)
	parser.add_argument(
		"--output-md",
		type=Path,
		default=Path(__file__).with_name("portfolio_health_report.md"),
		help="Path for health report (markdown) output.",
	)
	parser.add_argument("--model", type=str, default=MODEL, help="Local language model name.")
	parser.add_argument("--temperature", type=float, default=0.2, help="LLM temperature.")
	parser.add_argument("--limit", type=int, default=None, help="Number of threads.")
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	input_dir = args.input_dir
	if not input_dir.exists():
		raise FileNotFoundError(f"Input directory not found: {input_dir}")
	threads = load_threads(input_dir, args.limit)
	results: List[AnalysisResult] = []
	for file_path in threads:
		text = file_path.read_text(encoding="UTF-8")
		if not text.strip():
			continue
		result = analyze_thread(text, file_path.name, args.model, args.temperature)
		results.append(result)
	report = build_report(results)
	args.output_json.write_text(report.model_dump_json(indent=2), encoding="UTF-8")
	args.output_md.write_text(render_markdown(report), encoding="UTF-8")


if __name__ == "__main__":
	main()