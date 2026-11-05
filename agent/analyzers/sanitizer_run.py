"""Evidence-based Linux GCC ASan/UBSan grading (WSL2 on Windows)."""
from __future__ import annotations
import re
from agent.sanitizer_runner import run_sanitizer_cases, DEFAULT_FLAGS, ALLOWED_FLAGS

DEFAULT_PENALTIES={'memory_error':5.0,'undefined_behavior':3.0,'memory_leak':2.0}


def _find_issues(stderr):
    """Parse full Dr. Memory results.txt or legacy sanitizer diagnostics.

    Potential leaks and still-reachable allocations are not confirmed errors
    and must not trigger grade deductions.
    """
    issues = []
    seen = set()

    def add(kind, detail, text):
        match = re.search(r'\bstudent\.c(?::|\()(\d+)', text, re.I)
        line = int(match.group(1)) if match else None
        key = (kind, line, detail.lower() if line is None else '')
        if key not in seen:
            seen.add(key)
            issues.append({'type': kind, 'detail': detail[:240],
                           'line': line, 'diagnostic': text[:900]})

    patterns = [
        (r'ERROR: LeakSanitizer: detected memory leaks', 'memory_leak'),
        (r'ERROR: AddressSanitizer: ([^\r\n]+)', 'memory_error'),
        (r'(?m)^([^\r\n]*?runtime error: ([^\r\n]+))', 'undefined_behavior'),
    ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, stderr, re.IGNORECASE):
            if kind == 'memory_error':
                description = match.group(1).strip()
                classification = ('memory_leak' if description.lower().startswith('leak')
                                  else 'memory_error')
            elif kind == 'undefined_behavior':
                description = match.group(2).strip()
                classification = ('memory_error' if re.search(
                    r'(out of bounds|out-of-bounds|null pointer|misaligned address|invalid pointer)',
                    description, re.I) else kind)
            else:
                description, classification = 'memory leak', kind
            add(classification, description,
                stderr[match.start():match.start()+1800])
    return issues


def _select_cases(cases,max_cases):
    normal=[c for c in cases if c.get('category')=='normal']
    edge=[c for c in cases if c.get('category')=='edge']
    selected=[]
    while len(selected)<max_cases and (normal or edge):
        if normal:selected.append(normal.pop(0))
        if edge and len(selected)<max_cases:selected.append(edge.pop(0))
    return selected


def grade_memory_safety(source,cases,*,time_limit_s,memory_limit_mb,
                        flags=DEFAULT_FLAGS,max_cases=10,penalties=None,
                        runner=run_sanitizer_cases):
    if flags not in ALLOWED_FLAGS:raise ValueError('Invalid sanitizer flags.')
    if isinstance(max_cases,bool) or not isinstance(max_cases,int) or not 1<=max_cases<=60:
        raise ValueError('max_cases must be 1-60.')
    penalties={**DEFAULT_PENALTIES,**(penalties or {})}
    chosen=_select_cases(cases,max_cases)
    if not chosen:
        return {'status':'not_configured','score':None,'feedback':'No approved test inputs.',
                'evidence':{'total':0,'executed':0,'findings':[],'tests':[]}}
    result=runner(source,[c['stdin'] for c in chosen],flags=flags,
                  time_limit_s=time_limit_s,memory_limit_mb=memory_limit_mb)
    if result.get('status')!='completed' or len(result.get('runs',[]))!=len(chosen):
        return {'status':'unavailable','score':None,
                'feedback':result.get('reason','Memory-check tool returned incomplete results.'),
                'evidence':{'total':len(chosen),'executed':0,'findings':[],'tests':[],
                            'tool':result.get('tool'),
                            'backend':result.get('backend')}}
    seen=set();findings=[];details=[];incomplete=False
    complete_checks=0
    failed_cases=[]
    for case,run in zip(chosen,result['runs']):
        issues=_find_issues(run.get('stderr',''))
        reason = run.get('reason','')
        if run.get('status') not in ('ok','nonzero_exit'):
            inconclusive=True
            reason = reason or f'Memory checker status: {run.get("status", "unknown")}'
        elif run.get('status')=='nonzero_exit' and not issues:
            inconclusive=True
            reason = reason or 'Program/tool exited nonzero without a recognized memory finding.'
        else:
            inconclusive=False
            complete_checks += 1
        if inconclusive:
            incomplete=True
            failed_cases.append({'case_id':case.get('id'),
                                 'title':case.get('title',''),
                                 'status':run.get('status','unknown'),
                                 'reason':reason})
        for issue in issues:
            key=(issue['type'],issue['line'],issue['detail'] if issue['line'] is None else '')
            if key not in seen:
                seen.add(key)
                findings.append({**issue,'case_id':case.get('id'),
                                 'case_title':case.get('title',''),'stdin':case['stdin']})
        details.append({'case_id':case.get('id'),'title':case.get('title',''),
                        'category':case.get('category'),'status':run.get('status'),
                        'findings':[i['type'] for i in issues],
                        'reason':reason,
                        'exit_code':run.get('exit_code'),
                        'stderr':run.get('stderr','')[:1200],
                        'tool_stderr':run.get('tool_stderr','')[:800],
                        'report_verified':run.get('report_verified'),
                        'wall_time_ms':run.get('wall_time_ms')})
    deduction=sum(penalties[f['type']] for f in findings)
    score=None if incomplete else round(max(0,10-deduction)/10,6)
    status='incomplete' if incomplete else 'completed'
    feedback=(f'{len(findings)} distinct memory-check findings. '
              f'{complete_checks}/{len(chosen)} checks completed; '
              f'{len(failed_cases)} inconclusive. Grade withheld. '
              f'First issue: {failed_cases[0]["title"] or failed_cases[0]["case_id"]}: '
              f'{failed_cases[0]["reason"][:250]}' if incomplete else
              f'{len(findings)} distinct memory-check findings across {len(chosen)} approved tests. '
              'An issue-free run does not prove memory safety.')
    if result.get('coverage_note'):
        feedback += ' ' + result['coverage_note']
    return {'status':status,'score':score,'feedback':feedback,
            'evidence':{'total':len(chosen),'executed':len(details),'findings':findings,
                        'tests':details,'attempted':len(details),
                        'completed_checks':complete_checks,
                        'inconclusive_checks':len(failed_cases),
                        'inconclusive_cases':failed_cases,
                        'tool':result.get('tool','asan_ubsan'),
                        'backend':result.get('backend'),
                        'memory_tool':result.get('memory_tool'),
                        'flags':result.get('flags',flags),
                        'requested_flags':flags,'compiler':result.get('compiler'),
                        'coverage_note':result.get('coverage_note',''),
                        'penalties':penalties,
                        'sanitizer_memory_limit_mb':result.get('sanitizer_memory_limit_mb'),
                        'sanitizer_time_limit_s':result.get('sanitizer_time_limit_s'),
                        'leak_detection_note':'Leak detection depends on sanitizer availability.'}}


def analyze_sanitizer_run(context,params):
    penalties={k:params.get('penalty_'+k,value) for k,value in DEFAULT_PENALTIES.items()}
    return grade_memory_safety(context['source'],context['cases'],
                               time_limit_s=context['time_limit_s'],
                               memory_limit_mb=context['memory_limit_mb'],
                               flags=params.get('flags',DEFAULT_FLAGS),
                               max_cases=params.get('max_cases',10),penalties=penalties)
