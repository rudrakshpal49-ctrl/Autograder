"""Independent grading analyzers for C submissions."""

from agent.analyzers.compiler_warnings import analyze_compiler_warnings
from agent.analyzers.static_analysis import analyze_structure
from agent.analyzers.function_split import analyze_modularity
from agent.analyzers.llm_review import analyze_readability
from agent.analyzers.similarity_flag import analyze_originality
from agent.analyzers.io_tests import analyze_io_tests
from agent.analyzers.edge_case_runner import analyze_edge_case_runner
from agent.analyzers.sanitizer_run import analyze_sanitizer_run
from agent.analyzers.time_growth_bridge import analyze_time_growth


HANDLERS = {
    'compiler_warnings': analyze_compiler_warnings,
    'static_analysis': analyze_structure,
    'function_split': analyze_modularity,
    'llm_review': analyze_readability,
    'similarity_flag': analyze_originality,
    'io_tests': analyze_io_tests,
    'edge_case_runner': analyze_edge_case_runner,
    'sanitizer_run': analyze_sanitizer_run,
    'complexity_estimator': analyze_time_growth,
}
