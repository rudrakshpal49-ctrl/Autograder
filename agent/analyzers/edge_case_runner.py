"""Logical (edge) analyzer: only approved edge cases, independent score."""
from agent.analyzers.io_tests import grade_cases
from services.edge_scenarios import edge_type_from_rationale


def grade_edge_cases(source, cases, *, time_limit_s, memory_limit_mb, runner=None,
                     output_comparison='exact', requested_edge_types=None):
    options = {'runner': runner} if runner is not None else {}
    selected = [c for c in cases if c['category'] == 'edge']
    result = grade_cases(source, selected, time_limit_s=time_limit_s,
                         memory_limit_mb=memory_limit_mb,
                         output_comparison=output_comparison, **options)
    requested = list(requested_edge_types or [])
    covered = sorted({edge_type_from_rationale(c.get('rationale', ''))
                      for c in selected if edge_type_from_rationale(c.get('rationale', ''))})
    result['evidence'].update({'requested_edge_types': requested,
                               'covered_edge_types': covered,
                               'missing_edge_types': [name for name in requested if name not in covered]})
    if requested and result['evidence']['missing_edge_types']:
        result['feedback'] += (' Uncovered configured scenarios: '
                               + ', '.join(result['evidence']['missing_edge_types']) + '.')
    return result


def analyze_edge_case_runner(context: dict, params: dict) -> dict:
    """Coordinator-compatible logical handler."""
    return grade_edge_cases(context['source'], context['cases'],
                            time_limit_s=context['time_limit_s'],
                            memory_limit_mb=context['memory_limit_mb'],
                            output_comparison=params.get('output_comparison', 'exact'),
                            requested_edge_types=params.get('cases', []))
