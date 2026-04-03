import sqlite3, json, uuid, pathlib
workflow = json.loads(pathlib.Path('n8n/workflow_agent_chat.json').read_text(encoding='utf-8'))
conn = sqlite3.connect('/data/database.sqlite')
cur = conn.cursor()
workflow_id = 'b2c3d4e5f6a7agentchat'
name = workflow['name']
existing = cur.execute('select id,name from workflow_entity where id=? or name=?', (workflow_id, name)).fetchall()
print('existing', existing)
if not existing:
    version_id = str(uuid.uuid4())
    cur.execute(
        '''insert into workflow_entity
        (id,name,active,nodes,connections,settings,staticData,pinData,versionId,triggerCount,meta,isArchived,versionCounter,activeVersionId)
        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            workflow_id,
            name,
            1 if workflow.get('active', False) else 0,
            json.dumps(workflow['nodes'], ensure_ascii=False),
            json.dumps(workflow['connections'], ensure_ascii=False),
            json.dumps(workflow.get('settings', {}), ensure_ascii=False),
            None,
            json.dumps(workflow.get('pinData', {}), ensure_ascii=False),
            version_id,
            0,
            None,
            0,
            1,
            version_id,
        ),
    )
    cur.execute('insert into shared_workflow (workflowId, projectId, role) values (?,?,?)', (workflow_id, 'ajMZRD7F7BgNsH2O', 'workflow:owner'))
    cur.execute('insert into webhook_entity (workflowId, webhookPath, method, node, webhookId, pathLength) values (?,?,?,?,?,?)', (workflow_id, 'agent-chat', 'POST', 'Webhook', 'agent-chat', 10))
    print('inserted workflow')
else:
    print('workflow already present')
print('workflow rows:')
for row in cur.execute("select id,name,active from workflow_entity where name='Orion Agent Chat'"):
    print(row)
print('shared rows:')
for row in cur.execute("select workflowId, projectId, role from shared_workflow where workflowId='b2c3d4e5f6a7agentchat'"):
    print(row)
print('webhook rows:')
for row in cur.execute("select workflowId, webhookPath, method from webhook_entity where workflowId='b2c3d4e5f6a7agentchat'"):
    print(row)
conn.commit()
conn.close()
