/** 首页示意插画：每条产品线一张统一风格的矢量图，不使用真实生成图。
 * 画布 300×300，用 slice 铺满容器；内容只画在中间安全区（x 40–260, y 70–230），
 * 这样 16:9 的产品线卡片和 3:4 的样图格都不会裁到主体。颜色全部走主题变量（见 home.css .kk-art）。 */

export type HomeArtKind = 'ecom' | 'daihuo' | 'avatar' | 'video' | 'drama' | 'canvas'

/* 电商图：商品主图 + 价格签 + 三张套图缩略 */
function Ecom() {
  return (
    <>
      <rect className="pa st" x="70" y="74" width="104" height="122" rx="10" />
      <rect className="ink" x="112" y="98" width="20" height="12" rx="3" />
      <rect className="ac st" x="102" y="110" width="40" height="70" rx="12" />
      <rect className="pa" x="110" y="130" width="24" height="26" rx="4" />
      <path className="cp st" d="M150 92h30l10 10-10 10h-30z" />
      <rect className="pa st" x="70" y="204" width="30" height="24" rx="5" />
      <rect className="pa st" x="107" y="204" width="30" height="24" rx="5" />
      <rect className="pa st" x="144" y="204" width="30" height="24" rx="5" />
      <rect className="pa st" x="190" y="122" width="62" height="74" rx="8" />
      <rect className="sk" x="200" y="134" width="42" height="6" rx="3" />
      <rect className="sk" x="200" y="148" width="30" height="6" rx="3" />
      <rect className="ac" x="200" y="174" width="34" height="12" rx="6" />
    </>
  )
}

/* 带货视频：竖屏视频 + 播放键 + 商品卡 + 点赞 */
function Daihuo() {
  return (
    <>
      <rect className="pa st" x="100" y="72" width="96" height="156" rx="12" />
      <circle className="ac st" cx="148" cy="138" r="20" />
      <path className="pa" d="M142 128v20l16-10z" />
      <rect className="cp st" x="112" y="186" width="72" height="28" rx="6" />
      <rect className="sk" x="120" y="194" width="22" height="12" rx="3" />
      <rect className="sk" x="148" y="196" width="28" height="4" rx="2" />
      <rect className="sk" x="148" y="203" width="18" height="4" rx="2" />
      <path className="ac st" d="M222 118c-6-8-18-4-16 6 2 8 16 16 16 16s14-8 16-16c2-10-10-14-16-6z" />
      <circle className="pa st" cx="222" cy="168" r="10" />
      <path className="st nf" d="M64 110h24M58 130h30M68 150h20" />
    </>
  )
}

/* 数字人口播：人像 + 麦克风 + 字幕条 + 声波 */
function Avatar() {
  return (
    <>
      <rect className="pa st" x="72" y="72" width="156" height="156" rx="12" />
      <path className="ac st" d="M104 228c0-30 20-46 46-46s46 16 46 46" />
      <circle className="cp st" cx="150" cy="140" r="26" />
      <path className="st nf" d="M138 136h4M158 136h4M142 150q8 6 16 0" />
      <rect className="ink" x="92" y="196" width="116" height="20" rx="4" />
      <rect className="cpf" x="102" y="203" width="60" height="6" rx="3" />
      <rect className="cpf" x="168" y="203" width="30" height="6" rx="3" />
      <path className="st nf" d="M240 120q8 20 0 40M252 110q14 30 0 60" />
      <path className="st nf" d="M60 120q-8 20 0 40M48 110q-14 30 0 60" />
    </>
  )
}

/* AI 短视频：三格胶片 + 时间线 */
function ShortVideo() {
  return (
    <>
      <rect className="ink" x="44" y="88" width="212" height="96" rx="8" />
      {[56, 124, 192].map((x, i) => (
        <g key={x}>
          <rect className={i === 1 ? 'cp' : 'pa'} x={x} y="100" width="56" height="72" rx="4" />
          <circle className="ac" cx={x + 40} cy="118" r="8" />
          <path className="ac" d={`M${x} 172l18-26 14 16 10-10 14 20z`} />
        </g>
      ))}
      {[52, 72, 92, 112, 132, 152, 172, 192, 212, 232].map((x) => (
        <rect key={x} className="pa" x={x} y="92" width="8" height="4" rx="1" />
      ))}
      <rect className="pa st" x="44" y="200" width="212" height="22" rx="11" />
      <rect className="ac" x="50" y="206" width="104" height="10" rx="5" />
      <circle className="cp st" cx="154" cy="211" r="9" />
    </>
  )
}

/* AI 短剧：2×2 分镜格 + 场记板 */
function Drama() {
  const panels = [
    { x: 62, y: 76, fill: 'pa' },
    { x: 152, y: 76, fill: 'cp' },
    { x: 62, y: 152, fill: 'cp' },
    { x: 152, y: 152, fill: 'pa' },
  ]
  return (
    <>
      {panels.map((p, i) => (
        <g key={i}>
          <rect className={`${p.fill} st`} x={p.x} y={p.y} width="84" height="68" rx="6" />
          <circle className="ac" cx={p.x + (i % 2 ? 56 : 28)} cy={p.y + 26} r="9" />
          <path className="ac" d={`M${p.x + (i % 2 ? 42 : 14)} ${p.y + 68}q14-26 28 0z`} />
        </g>
      ))}
      <g transform="rotate(-8 230 212)">
        <rect className="ink" x="200" y="198" width="60" height="34" rx="4" />
        <path className="pa st" d="M200 186h60v12h-60z" />
        <path className="ink" d="M212 186l-6 12h8l6-12zM232 186l-6 12h8l6-12zM252 186l-6 12h8l6-12z" />
      </g>
    </>
  )
}

/* 自由画布：节点 + 连线 */
function Canvas() {
  return (
    <>
      <path className="st nf dash" d="M118 112C150 112 140 150 172 150M118 196C150 196 140 160 172 156" />
      <rect className="pa st" x="58" y="92" width="62" height="40" rx="8" />
      <rect className="ac" x="68" y="104" width="16" height="16" rx="4" />
      <rect className="sk" x="90" y="106" width="22" height="5" rx="2.5" />
      <rect className="sk" x="90" y="115" width="14" height="5" rx="2.5" />
      <rect className="pa st" x="58" y="176" width="62" height="40" rx="8" />
      <circle className="cp st" cx="76" cy="196" r="8" />
      <rect className="sk" x="90" y="190" width="22" height="5" rx="2.5" />
      <rect className="sk" x="90" y="199" width="14" height="5" rx="2.5" />
      <rect className="pa st" x="170" y="118" width="80" height="70" rx="8" />
      <rect className="cp" x="178" y="126" width="64" height="40" rx="4" />
      <path className="ac" d="M178 166l20-22 14 14 10-8 20 16z" />
      <rect className="sk" x="178" y="174" width="40" height="5" rx="2.5" />
      <circle className="ac st" cx="120" cy="112" r="4" />
      <circle className="ac st" cx="120" cy="196" r="4" />
      <circle className="ac st" cx="170" cy="153" r="4" />
    </>
  )
}

const SCENES: Record<HomeArtKind, () => React.JSX.Element> = {
  ecom: Ecom,
  daihuo: Daihuo,
  avatar: Avatar,
  video: ShortVideo,
  drama: Drama,
  canvas: Canvas,
}

/** 渲染一张示意插画；纯装饰，对读屏隐藏。 */
export default function HomeArt({ kind }: { kind: HomeArtKind }) {
  const Scene = SCENES[kind]
  return (
    <svg className="kk-art" viewBox="0 0 300 300" preserveAspectRatio="xMidYMid slice" aria-hidden focusable="false">
      <rect className="bg" width="300" height="300" />
      <circle className="dot" cx="250" cy="70" r="34" />
      <circle className="dot" cx="46" cy="236" r="22" />
      {/* 主体整体缩小一点，卡片四周留白 */}
      <g transform="translate(150 150) scale(0.86) translate(-150 -150)">
        <Scene />
      </g>
    </svg>
  )
}
