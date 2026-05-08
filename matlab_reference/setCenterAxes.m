function setCenterAxes(fig)

% Figure
figure(fig);

% Fix Right Axis
yyaxis right;
ylimR = ylim;
ylimR_big = max(abs(ylimR));
ylimR_min = -ylimR_big;
ylimR_max = ylimR_big;
ylim([ylimR_min ylimR_max]);

% Fix Left Axis
yyaxis left;
ylimL = ylim;
ylimL_big = max(abs(ylimL));
ylimL_min = -ylimL_big;
ylimL_max = ylimL_big;
ylim([ylimL_min ylimL_max]);

end