/** 关闭订单详情时一并清掉 order 与 trade 深链参数；trade 留在地址里会让详情被再次打开。 */
export function withoutOrderDeepLink(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete("order");
  next.delete("trade");
  return next;
}
