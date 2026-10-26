import { Link } from 'react-router-dom'
import { labels, type Row } from './api'
import RightsPage from './RightsPage'
import AuthorizationPanel from './AuthorizationPanel'

// 发布步骤始终显示；缺少视频时明确指向第一步，不再隐藏权属入口。
export default function WorkPublishingSteps({ item, onUpdated }: { item: Row; onUpdated: () => void }) {
  const mediaReady = Boolean(item.asset_id && item.media_status === 'ready')
  const internal = item.source === 'printfilm'
  const sourceKnown = internal || item.source === 'external'
  const rightsTitle = internal ? '确认版权信息' : sourceKnown ? '上传权属证明 / 客服协办' : '确认生成平台并办理权属'
  return <section className="pf-market-publishing" id="publishing" aria-label="作品发布流程">
    <h2>发布步骤</h2><p>按顺序完成文件、授权材料与人工审核，最后由您主动上架。</p>
    <ol className="pf-market-publish-steps">
      <li data-complete={mediaReady}><span>01</span><div><h3>上传并绑定视频</h3><p>{mediaReady ? '视频已上传' : item.asset_id ? `媒体${labels[item.media_status || ''] || '尚未就绪'}，请检查文件状态` : '尚未绑定视频。请先上传视频。'}</p><Link to={`/marketplace/works/${item.id}/edit`}>{mediaReady ? '修改作品文件' : '去上传 / 绑定视频'}</Link></div></li>
      <li data-complete={Boolean(item.rights_complete)}><span>02</span><div><h3>{rightsTitle}</h3><p>{internal ? '签署授权函即可，无需其他证明。' : sourceKnown ? '填写证书编号并上传截图或照片；没有版权联系客服协办。' : '请先确认生成平台，再按对应流程处理。'}</p><p>作品审核：{labels[item.audit_status || 'missing']}</p>{item.rights_ready ? <p>作品审核已通过</p> : <p>{mediaReady ? '完成材料后提交管理员审核。' : '请先完成第 01 步上传视频。'}</p>}</div></li>
      <li data-complete={item.audit_status === 'approved'}><span>03</span><div><h3>提交作品审核</h3><p>作品和版权信息一次提交，管理员一次审核。免费作品审核通过后公开播放原片；版权咨询作品生成最多15秒且不超过原片30%的水印试看；平台投放作品生成同源分辨率播放版本。</p></div></li>
      <li data-complete={Boolean(item.listed)}><span>04</span><div><h3>确认上架</h3><p>{item.listed ? '已上架' : '媒体、权属和内容均通过后，由您主动上架。'}</p></div></li>
    </ol>
    {mediaReady ? <details className="pf-market-inline-rights" open={!item.rights_ready}><summary>{internal ? '签署 / 查看授权函' : '上传 / 补充权属材料，或联系客服协办'}</summary><RightsPage key={`${item.asset_id}-${item.source}`} assetId={item.asset_id!} sourceHint={item.source} embedded onUpdated={onUpdated} /></details> : internal ? <AuthorizationPanel /> : <p className="pf-market-notice">版权信息表单，会在视频绑定且处理就绪后显示。<Link to="/marketplace/authorization">PrintFilm 作品也可先签署账号授权函</Link>。</p>}
  </section>
}




