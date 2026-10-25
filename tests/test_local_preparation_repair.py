import pytest
from agent_workflow_kernel import WorkflowLedger,WorkflowInstance,WorkflowStatus,StageRun,StageRunStatus,FailureClass,LedgerConflict


def fixture(tmp_path,adapter='lane.prepare'):
    ledger=WorkflowLedger(tmp_path/'ledger.sqlite3');ledger.initialize()
    ledger.insert_workflow_instance(WorkflowInstance(instance_id='i',workflow_def_id='w',workflow_version='1',
        status=WorkflowStatus.BLOCKED,current_stage_id='review',input_hash='input'),workflow_definition_hash='exact')
    ledger.insert_stage_run(StageRun(stage_run_id='i:prepare:1',instance_id='i',stage_id='prepare',
        adapter_id=adapter,status=StageRunStatus.SUCCEEDED),input_hash='input')
    ledger.insert_stage_run(StageRun(stage_run_id='i:review:1',instance_id='i',stage_id='review',
        adapter_id='surface.review',status=StageRunStatus.BLOCKED,failure_class=FailureClass.INVALID_OUTPUT),input_hash='input')
    args=dict(instance_id='i',expected_workflow_hash='exact',expected_blocked_stage_run_id='i:review:1',
        checkpoint_stage_run_id='i:prepare:1',reason='repair exact local package',verified_no_external_effect=True,operator_reauthorized=True)
    return ledger,args


def test_repair_appends_checkpoint_and_preserves_invalid_review(tmp_path):
    ledger,args=fixture(tmp_path)
    result=ledger.queue_local_preparation_repair(**args)
    assert result['stage_run_id']=='i:prepare:2'
    assert ledger.get_stage_run('i:review:1').status==StageRunStatus.BLOCKED
    assert ledger.get_workflow_instance('i').current_stage_id=='prepare'
    assert ledger.list_events()[-1]['event_type']=='local_preparation_repair_queued'
    with pytest.raises(LedgerConflict):ledger.queue_local_preparation_repair(**args)
    ledger.close()


@pytest.mark.parametrize('mutation',['hash','host','runtime','decision','authorization','stale_attempt'])
def test_repair_refuses_authority_and_effect_boundary_changes(tmp_path,mutation):
    ledger,args=fixture(tmp_path,adapter='host.publish' if mutation=='host' else 'runtime.agent' if mutation=='runtime' else 'lane.prepare')
    if mutation=='hash':args['expected_workflow_hash']='other'
    if mutation=='authorization':args['operator_reauthorized']=False
    if mutation=='decision':
        # Use the public decision receipt API; no human decision may be crossed.
        from agent_workflow_kernel.policy import HumanApprovalReceipt
        ledger.record_human_decision(HumanApprovalReceipt(approval_id='d',gate_id='gate',decision='approved',human_ref='operator',canonical_surface='test',exact_action_approved='human_decision',action_fingerprint='fp'),instance_id='i',stage_run_id='i:review:1')
    if mutation=='stale_attempt':
        ledger.insert_stage_run(StageRun(stage_run_id='i:review:2',instance_id='i',stage_id='review',attempt=2,adapter_id='surface.review',status=StageRunStatus.BLOCKED,failure_class=FailureClass.INVALID_OUTPUT),input_hash='input')
    with pytest.raises((ValueError,LedgerConflict)):ledger.queue_local_preparation_repair(**args)
    assert ledger.get_workflow_instance('i').status==WorkflowStatus.BLOCKED
    ledger.close()
