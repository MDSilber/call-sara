/** Modular ECharts: only the pieces the six rooms draw. SVG renderer to
 * match Sara Home (crisp at every DPI, themeable via CSS-read colors). */
import { BarChart, LineChart, PieChart, ScatterChart, TreemapChart } from 'echarts/charts'
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'

echarts.use([
  LineChart, BarChart, PieChart, ScatterChart, TreemapChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  MarkLineComponent, SVGRenderer,
])

export { echarts }
export type { EChartsCoreOption } from 'echarts/core'
