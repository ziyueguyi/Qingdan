from qingdan.core.context import init, shutdown
from qingdan.core.registry import call
import qingdan.bootstrap
import qingdan.__main__ as app
init('.review_qa')
if call('group.list')['total'] == 0:
    for i in range(27):
        call('group.create', name=f'QA 清单 {i:02}')
    call('task.create', title='QA 标签来源', tags=[f'QA 标签 {i:02}' for i in range(27)])
shutdown()
app._free_port = lambda: 57701
raise SystemExit(app.serve('.review_qa'))
