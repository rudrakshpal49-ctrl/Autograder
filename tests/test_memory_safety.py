"""Sanitizer detection, grading and rule integration."""
import json
import pytest
from agent.analyzers.sanitizer_run import _find_issues, grade_memory_safety
from agent.analyzers import HANDLERS
from services.first_pass_service import attach_memory_safety_grading
from services.rules_service import validate_rules, rules_warnings


def cases(n=3):
    return [{'id':i+1,'title':str(i),'category':'normal' if i%2==0 else 'edge',
             'stdin':str(i)+'\n'} for i in range(n)]


def test_analyzer_registered():
    assert 'sanitizer_run' in HANDLERS


def test_penalties_dedup_and_source_lines():
    def fake(source,inputs,**kw):
        return {'status':'completed','runs':[
            {'status':'ok','stderr':''},
            {'status':'nonzero_exit','stderr':'ERROR: AddressSanitizer: heap-buffer-overflow\n#0 0x123 main /tmp/student.c:14:2'},
            {'status':'nonzero_exit','stderr':'/tmp/student.c:22:3: runtime error: signed integer overflow\n'}]}
    r=grade_memory_safety(b'',cases(),time_limit_s=1,memory_limit_mb=64,runner=fake)
    assert r['status']=='completed' and r['score']==.2
    assert [f['type'] for f in r['evidence']['findings']]==['memory_error','undefined_behavior']
    assert r['evidence']['findings'][0]['line']==14


def test_ubsan_oob_is_memory_error():
    assert _find_issues('/tmp/student.c:9:2: runtime error: index 4 out of bounds for int [3]\n')[0]['type']=='memory_error'


def test_leak_deduplicated():
    def fake(source,inputs,**kw):
        return {'status':'completed','runs':[{'status':'nonzero_exit','stderr':
            'ERROR: LeakSanitizer: detected memory leaks\n'}]*len(inputs)}
    r=grade_memory_safety(b'',cases(),time_limit_s=1,memory_limit_mb=64,runner=fake)
    assert r['score']==.8 and len(r['evidence']['findings'])==1


def test_runtime_unavailable_or_timeout_is_ungraded():
    unavailable=lambda *a,**kw:{'status':'unavailable','reason':'no support','runs':[]}
    r=grade_memory_safety(b'',cases(),time_limit_s=1,memory_limit_mb=64,runner=unavailable)
    assert r['score'] is None and r['status']=='unavailable'
    def timed(source,inputs,**kw):
        return {'status':'completed','runs':[{'status':'timeout','stderr':''}]*len(inputs)}
    r=grade_memory_safety(b'',cases(),time_limit_s=1,memory_limit_mb=64,runner=timed)
    assert r['score'] is None and r['status']=='incomplete'


@pytest.mark.parametrize('params',[{'flags':'-fplugin=x'},{'flags':['-fsanitize=address']},
   {'max_cases':0},{'max_cases':61},{'max_cases':True},{'penalty_memory_leak':-1},
   {'penalty_memory_error':11}])
def test_invalid_params(params):
    rule={'time_limit_s':1,'memory_limit_mb':64,'metrics':[{'name':'mem','analyzer':'sanitizer_run','params':params}]}
    with pytest.raises(ValueError):validate_rules(json.dumps(rule).encode())


def test_rule_supported_no_inactive_warning():
    rule={'time_limit_s':1,'memory_limit_mb':64,'metrics':[{'name':'mem','analyzer':'sanitizer_run',
        'params':{'flags':'-fsanitize=address,undefined','max_cases':10}}]}
    assert not any('not yet implemented' in w for w in rules_warnings(validate_rules(json.dumps(rule).encode())))


def test_service_skip_and_dispatch(monkeypatch):
    called=[]
    monkeypatch.setitem(HANDLERS,'sanitizer_run',lambda context,params:
        called.append(params) or {'status':'completed','score':1,'feedback':'OK','evidence':{}})
    rules={'time_limit_s':1,'memory_limit_mb':64,'metrics':[{'name':'memory_safety','analyzer':'sanitizer_run',
         'weight':.1,'params':{'max_cases':10}}]}
    report={'status':'ready','smoke':{'status':'passed'}}
    attach_memory_safety_grading(report,rules,b'c',cases())
    assert report['memory_safety_grading']['score']==1 and len(called)==1
    report={'status':'blocked','smoke':{'status':'timeout'}}
    attach_memory_safety_grading(report,rules,b'c',cases())
    assert report['memory_safety_grading']['status']=='skipped' and len(called)==1
