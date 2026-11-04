import { zhWorkflow } from './zh/workflow'
import { zhPages } from './zh/pages'
import { zhShell } from './zh/shell'
import { zhEcomSet } from './zh/ecomSet'
import { zhCompliance } from './zh/compliance'
import { zhDaihuo } from './zh/daihuo'

export const zh = {
  ...zhWorkflow,
  ...zhShell,
  ...zhPages,
  ...zhEcomSet,
  ...zhCompliance,
  ...zhDaihuo,
}
