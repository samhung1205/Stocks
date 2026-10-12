/* Strategy Lab 共用圖表 builder。只組 ECharts option，不碰 API 或頁面狀態。 */
(function () {
  const instances = {};

  function chart(id) {
    if (instances[id]) instances[id].dispose();
    instances[id] = makeChart(id);
    return instances[id];
  }

  function normalized(rows, key) {
    const base = rows.find(r => Number(r[key]) > 0)?.[key];
    return rows.map(r => base && r[key] != null ? Number(r[key]) / Number(base) * 100 : null);
  }

  function drawdowns(rows, key) {
    let peak = null;
    return rows.map(r => {
      const value = Number(r[key]);
      if (!Number.isFinite(value) || value <= 0) return null;
      peak = peak == null ? value : Math.max(peak, value);
      return (value / peak - 1) * 100;
    });
  }

  function line(name, data, color, extra = {}) {
    return {
      name, type: "line", data, showSymbol: false, connectNulls: false,
      sampling: "lttb", animation: false,
      lineStyle: { width: 2, color, ...(extra.lineStyle || {}) },
      itemStyle: { color }, emphasis: { focus: "series" }, ...extra,
    };
  }

  function shared(rows, yAxis) {
    return baseOption({
      animation: false,
      aria: { enabled: true, decal: { show: true } },
      legend: { top: 0, textStyle: { color: CHART_THEME.MUTED } },
      grid: { left: 62, right: 22, top: 42, bottom: 42 },
      xAxis: { type: "category", boundaryGap: false, data: rows.map(r => r.date), ...axisStyle() },
      yAxis: { type: "value", scale: true, ...axisStyle(), ...yAxis },
      dataZoom: [{ type: "inside", start: 0, end: 100 },
                 { type: "slider", height: 14, bottom: 5, borderColor: CHART_THEME.BORDER,
                   fillerColor: "rgba(49,95,104,.14)", textStyle: { color: CHART_THEME.MUTED } }],
    });
  }

  function equity(id, rows, scale = "linear") {
    const net = normalized(rows, "net_equity");
    const gross = normalized(rows, "gross_equity");
    const benchmark = normalized(rows, "benchmark");
    const option = shared(rows, {
      type: scale === "log" ? "log" : "value",
      name: "起始值 = 100", nameTextStyle: { color: CHART_THEME.MUTED },
      axisLabel: { color: CHART_THEME.MUTED, formatter: v => Number(v).toFixed(0) },
    });
    option.aria.description = "策略淨值、未扣成本淨值與基準指數的歷史走勢，起始值均標準化為一百。";
    option.tooltip.formatter = points => {
      if (!points?.length) return "";
      return `<b>${points[0].axisValue}</b><br>` + points
        .filter(p => p.value != null)
        .map(p => `${p.marker}${p.seriesName}：${Number(p.value).toFixed(2)}`)
        .join("<br>");
    };
    option.series = [
      line("策略 Net", net, CHART_THEME.ACCENT),
      line("策略 Gross", gross, CHART_THEME.MUTED, { lineStyle: { type: "dashed", width: 1.5 } }),
      line("Benchmark", benchmark, CHART_THEME.WARN),
    ];
    chart(id).setOption(option);
  }

  function drawdown(id, rows) {
    const option = shared(rows, {
      min: value => Math.floor(value.min / 5) * 5,
      max: 0,
      axisLabel: { color: CHART_THEME.MUTED, formatter: v => `${Number(v).toFixed(0)}%` },
    });
    option.aria.description = "策略與基準指數相對各自歷史高點的回撤百分比。";
    option.tooltip.formatter = points => {
      if (!points?.length) return "";
      return `<b>${points[0].axisValue}</b><br>` + points
        .filter(p => p.value != null)
        .map(p => `${p.marker}${p.seriesName}：${Number(p.value).toFixed(2)}%`)
        .join("<br>");
    };
    option.series = [
      line("策略", drawdowns(rows, "net_equity"), CHART_THEME.ACCENT, {
        areaStyle: { color: "rgba(49,95,104,.10)" },
      }),
      line("Benchmark", drawdowns(rows, "benchmark"), CHART_THEME.WARN, {
        lineStyle: { type: "dashed", width: 1.5 },
      }),
    ];
    chart(id).setOption(option);
  }

  function heatmap(id, surface, pathLabel) {
    const xValues = surface.x?.values || [];
    const yValues = surface.y?.values || ["全部"];
    const xIndex = new Map(xValues.map((value, index) => [String(value), index]));
    const yIndex = new Map(yValues.map((value, index) => [String(value), index]));
    const finite = surface.cells.map(cell => Number(cell.value)).filter(Number.isFinite);
    const data = surface.cells.map(cell => ({
      value: [xIndex.get(String(cell.x)), yIndex.get(String(cell.y ?? "全部")), cell.value],
      detail: cell,
    }));
    const option = baseOption({
      animation: false,
      aria: {
        enabled: true,
        description: `${pathLabel(surface.x?.path)} 與 ${pathLabel(surface.y?.path || "全部")} 的參數表面熱圖，每格直接顯示 objective 數值。`,
        decal: { show: true },
      },
      grid: { left: 86, right: 28, top: 24, bottom: 70 },
      tooltip: {
        formatter: item => {
          const cell = item.data.detail;
          return `<b>${pathLabel(surface.x.path)}: ${cell.x}</b><br>`
            + `${pathLabel(surface.y?.path || "全部")}: ${cell.y ?? "全部"}<br>`
            + `${surface.objective?.name || "objective"}: ${Number(cell.value).toFixed(4)}<br>`
            + `n: ${cell.n} · min: ${Number(cell.min).toFixed(4)} · max: ${Number(cell.max).toFixed(4)}`;
        },
      },
      xAxis: { type: "category", name: pathLabel(surface.x?.path), data: xValues.map(String), ...axisStyle() },
      yAxis: { type: "category", name: pathLabel(surface.y?.path || "全部"), data: yValues.map(String), ...axisStyle() },
      visualMap: {
        min: finite.length ? Math.min(...finite) : 0,
        max: finite.length ? Math.max(...finite) : 1,
        calculable: false, orient: "horizontal", left: "center", bottom: 4,
        textStyle: { color: CHART_THEME.MUTED },
        inRange: { color: ["#edf0ec", "#d1e0de", "#aacac7", "#82afb0"] },
      },
      series: [{
        type: "heatmap", data,
        label: { show: true, color: CHART_THEME.TEXT, formatter: item => Number(item.data.detail.value).toFixed(3) },
        itemStyle: { borderColor: CHART_THEME.BORDER, borderWidth: 2 },
        emphasis: { itemStyle: { borderColor: CHART_THEME.TEXT, borderWidth: 2 } },
      }],
    });
    chart(id).setOption(option);
  }

  function independentOos(id, folds) {
    const maxPoints = Math.max(0, ...folds.map(f => f.points.length));
    const option = baseOption({
      animation: false,
      aria: {
        enabled: true,
        description: "各 Walk Forward fold 各自從現金開始、標準化為一百的獨立 OOS 淨值。橫軸是該 fold 內的交易日序號，不代表一條連續投資曲線。",
        decal: { show: true },
      },
      legend: { top: 0, textStyle: { color: CHART_THEME.MUTED } },
      grid: { left: 58, right: 24, top: 42, bottom: 42 },
      xAxis: { type: "value", min: 0, max: Math.max(1, maxPoints - 1), name: "Fold 內交易日", ...axisStyle() },
      yAxis: { type: "value", scale: true, name: "各自起始 = 100", ...axisStyle() },
      tooltip: {
        trigger: "item",
        formatter: item => `<b>${item.seriesName}</b><br>${item.data[2]}<br>交易日 ${item.data[0] + 1}：${Number(item.data[1]).toFixed(2)}`,
      },
      series: folds.map((fold, index) => {
        const base = fold.points.find(p => Number(p.net_equity) > 0)?.net_equity;
        const styles = ["solid", "dashed", "dotted", [8, 4, 2, 4]];
        return line(`Fold ${fold.index + 1}`, fold.points.map((p, i) => [i, base ? Number(p.net_equity) / Number(base) * 100 : null, p.date]),
          [CHART_THEME.ACCENT, "#70798a", "#8a6a32", "#4f7474"][index % 4],
          { lineStyle: { type: styles[index % styles.length], width: 2 } });
      }),
    });
    chart(id).setOption(option);
  }

  function stitchedOos(id, rows) {
    const option = shared(rows, {
      name: "起始值 = 100", nameTextStyle: { color: CHART_THEME.MUTED },
      axisLabel: { color: CHART_THEME.MUTED, formatter: v => Number(v).toFixed(0) },
    });
    option.aria.description = "次要證據：承接前一個 OOS 終端部位的 stitched OOS 連續淨值；不屬於 independent-fold evidence。";
    option.series = [line("Stitched OOS", normalized(rows, "net_equity"), CHART_THEME.WARN)];
    chart(id).setOption(option);
  }

  function portfolioSeries(id, rows, kind) {
    const option = shared(rows, {
      min: kind === "drawdown" ? value => Math.floor(value.min / 5) * 5 : undefined,
      max: kind === "drawdown" ? 0 : undefined,
      axisLabel: { color: CHART_THEME.MUTED, formatter: v =>
        kind === "equity" ? Number(v).toFixed(0) : `${Number(v).toFixed(0)}%` },
    });
    const series = kind === "equity" ? normalized(rows, "total_equity") :
      kind === "drawdown" ? drawdowns(rows, "total_equity") :
      rows.map(r => r.cash_weight == null ? null : Number(r.cash_weight) * 100);
    option.aria.description = kind === "equity" ? "Portfolio 歷史淨值，首日標準化為一百。" :
      kind === "drawdown" ? "Portfolio 相對歷史高點的逐日回撤百分比。" :
      "Portfolio 逐日現金權重；包含策略現金、無候選現金與執行後殘餘現金。";
    option.tooltip.formatter = points => points?.length ?
      `<b>${points[0].axisValue}</b><br>${points[0].marker}${Number(points[0].value).toFixed(2)}${kind === "equity" ? "" : "%"}` : "";
    option.series = [line(kind === "equity" ? "淨值（起始 = 100）" :
      kind === "drawdown" ? "回撤" : "現金權重", series,
      kind === "cash" ? CHART_THEME.WARN : CHART_THEME.ACCENT,
      kind === "drawdown" ? { areaStyle: { color: "rgba(49,95,104,.10)" } } : {})];
    chart(id).setOption(option);
  }

  window.StrategyCharts = { equity, drawdown, heatmap, independentOos, stitchedOos };
  window.PortfolioCharts = { series: portfolioSeries, drawdowns };
})();
