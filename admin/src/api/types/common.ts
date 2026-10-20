/** 管理接口通用分页元信息；列表仍由服务端筛选和分页。 */
export type PageMeta = {
  page: number;
  page_size: number;
  total: number;
};

/** 服务端分页列表的统一响应结构。 */
export type PageResult<T> = { items: T[]; meta: PageMeta };
