import hashlib
import pytest
from agent_workflow_kernel import WorkflowLedger, WorkflowInstance, WorkflowStatus, StageRun, StageRunStatus, Receipt, ArtifactRef, LedgerConflict


def fixture(tmp_path, adapter='runtime.agent', receipt_status='failed'):
    ledger=WorkflowLedger(tmp_path/'ledger.sqlite3');ledger.initialize()
    ledger.insert_workflow_instance(WorkflowInstance(instance_id='i',workflow_def_id='w',workflow_version='1',status=WorkflowStatus.BLOCKED,current_stage_id='s',input_hash='input'))
    ledger.insert_stage_run(StageRun(stage_run_id='i:s:1',instance_id='i',stage_id='s',attempt=1,status=StageRunStatus.BLOCKED,adapter_id=adapter),input_hash='input')
    ledger.record_receipt(Receipt(receipt_id='failed',kind='adapter.runtime.invoke',workflow_id='w',instance_id='i',stage_id='s',stage_run_id='i:s:1',status=receipt_status,summary='diagnostic failure',created_at='2026-10-25T00:00:00Z',artifact_refs=(ArtifactRef(artifact_id='a',role='draft',uri='memory://diagnostic',content_hash='hash',mime_type='application/json'),)))
    raw=ledger.connection.execute('select receipt_json from receipts where receipt_id="failed"').fetchone()[0]
    evidence={'stage_run_id':'i:s:1','source_receipt_id':'failed','source_receipt_sha256':hashlib.sha256(raw.encode()).hexdigest(),'artifact_hashes':{'a':'hash'},'verified_diagnostic_only':True,'verified_no_model_output':True,'verified_read_only_runtime':True,'verification_ref':'fixture:checked'}
    return ledger,evidence


def test_diagnostic_recovery_preserves_refs_and_appends_attempt(tmp_path):
    ledger,evidence=fixture(tmp_path)
    result=ledger.retry_blocked_stage_without_effect(instance_id='i',reason='verified diagnostic wrapper',verified_no_external_effect=True,operator_reauthorized=True,diagnostic_artifact_evidence=evidence)
    assert result['stage_run_id']=='i:s:2'
    assert ledger.get_stage_run('i:s:1').status==StageRunStatus.BLOCKED
    assert ledger.connection.execute('select count(*) from artifact_refs').fetchone()[0]==1
    assert ledger.list_events()[-1]['payload']['diagnostic_artifact_evidence']==evidence
    ledger.close()


@pytest.mark.parametrize('mutation',['missing','wrong_hash','wrong_receipt','no_reauthorization','host','successful_receipt'])
def test_durable_artifact_guard_remains_closed(tmp_path,mutation):
    ledger,evidence=fixture(tmp_path,adapter='host.publish' if mutation=='host' else 'runtime.agent',receipt_status='succeeded' if mutation=='successful_receipt' else 'failed')
    if mutation=='missing':evidence=None
    if mutation=='wrong_hash':evidence['artifact_hashes']['a']='changed'
    if mutation=='wrong_receipt':evidence['source_receipt_sha256']='wrong'
    with pytest.raises(LedgerConflict):
        ledger.retry_blocked_stage_without_effect(instance_id='i',reason='verify',verified_no_external_effect=True,operator_reauthorized=mutation!='no_reauthorization',diagnostic_artifact_evidence=evidence)
    assert ledger.get_workflow_instance('i').status==WorkflowStatus.BLOCKED
    ledger.close()
