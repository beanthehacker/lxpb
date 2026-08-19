"""
Render 1-second ES candlesticks with BidVolume and AskVolume subplots below,
for a given time-of-day window, as an interactive (zoom/pan) HTML chart.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from absorption_backtest import build_features, flag_events

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")
OUT_HTML = os.path.join(_HERE, "ES_20260813_0858-0901_PT_candles.html")

START_TIME = "08:58:00"
END_TIME = "09:01:00"


def main():
    full = pd.read_csv(CSV_PATH, index_col="Time_PT", parse_dates=True)
    full = flag_events(build_features(full))  # compute absorption flags on full session
    df = full.between_time(START_TIME, END_TIME)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.6, 0.2, 0.2],
        vertical_spacing=0.04,
        subplot_titles=("ES 1s Candles", "Bid Volume", "Ask Volume"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="ES",
            increasing_line_color="green",
            decreasing_line_color="red",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(x=df.index, y=df["BidVolume"], name="Bid Volume", marker_color="crimson"),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Bar(x=df.index, y=df["AskVolume"], name="Ask Volume", marker_color="seagreen"),
        row=3,
        col=1,
    )

    # Overlay absorption-event markers on the candlestick panel
    sell_abs = df[df["SellAbsorption"]]
    buy_abs = df[df["BuyAbsorption"]]
    if not sell_abs.empty:
        fig.add_trace(
            go.Scatter(
                x=sell_abs.index, y=sell_abs["Low"] - 0.5,
                mode="markers+text", name="Sell Absorption (Bullish)",
                marker=dict(symbol="triangle-up", size=14, color="lime", line=dict(width=1, color="black")),
                text=["ABSORB↑"] * len(sell_abs), textposition="bottom center",
            ),
            row=1, col=1,
        )
    if not buy_abs.empty:
        fig.add_trace(
            go.Scatter(
                x=buy_abs.index, y=buy_abs["High"] + 0.5,
                mode="markers+text", name="Buy Absorption (Bearish)",
                marker=dict(symbol="triangle-down", size=14, color="orange", line=dict(width=1, color="black")),
                text=["ABSORB↓"] * len(buy_abs), textposition="top center",
            ),
            row=1, col=1,
        )

    fig.update_layout(
        title=f"ES 1s Candles + Bid/Ask Volume — {START_TIME} to {END_TIME} PT (2026-08-13)",
        xaxis3_title="Time (PT)",
        height=900,
        dragmode="zoom",  # click-drag box zoom; scroll to zoom via config below
        bargap=0.1,
        showlegend=False,
        hovermode="x unified",
    )
    # Disable rangeslider on the candlestick x-axis (we use drag/scroll zoom instead)
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    fig.update_xaxes(matches="x")

    config = {
        "scrollZoom": True,  # mouse-wheel zoom
        "displaylogo": False,
        "modeBarButtonsToAdd": ["pan2d", "zoomIn2d", "zoomOut2d", "resetScale2d"],
    }
    fig.write_html(OUT_HTML, config=config, include_plotlyjs="cdn")
    print(f"Wrote {len(df)} rows -> {OUT_HTML}")


if __name__ == "__main__":
    main()
