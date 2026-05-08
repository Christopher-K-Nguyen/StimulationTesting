function obj = boxchart2(x,y)
%% Constants
WIDTH = 960;
HEIGHT = 720;

%% Variables
x_len = length(x);
[row,col] = size(y);
y_len = row * col;
xgroupdata = zeros(y_len,1);
ygroupdata = zeros(y_len,1);

%% Function
for idx = 1:row
    idx_min = col * (idx - 1) + 1;
    idx_max = col * idx;
    idx_arr = idx_min:idx_max;
    xgroupdata(idx_arr) = x(idx);
    y_arr = y(idx,:);
    ygroupdata(idx_arr) = y_arr(:);
end

if row > 10
    boxWidth = 0.75;
else
    boxWidth = 0.5;
end

if x_len < 10
    boxWidth = 1;
end

% Calculate quartiles and IQR
% quantile1 = quantile(data, 0.25);
% quantile3 = quantile(data, 0.75);
% iqr = quantile3 - quantile1;

% Calculate the adjusted whisker caps to include actual min and max but exclude outliers
% yMin = min(y);
% yMax = max(y);
% lowerWhisker = max(yMin, quantile1 - 1.5*iqr);
% upperWhisker = min(yMax, quantile3 + 1.5*iqr);

empty_tf = ygroupdata == 0 | isnan(ygroupdata);
ygroupdata(empty_tf) = [];
xgroupdata(empty_tf) = [];
obj = boxchart(xgroupdata,ygroupdata, ...
    'BoxFaceColor','none', ...
    'BoxEdgeColor','k', ...
    'BoxWidth',boxWidth, ...
    'WhiskerLineColor','k', ...
    'JitterOutliers','on', ...
    'MarkerStyle','none');
box on;
[x_min,x_max] = getLimits(x);
xlim([x_min x_max]);
xlim('padded');
fig = gcf;
ax = gca;
if rem(x_max,200) == 0
    xticks_new = x_min:200:x_max;
elseif rem(x_max,100) == 0
    xticks_new = x_min:100:x_max;
elseif rem(x_max,60) == 0
    xticks_new = x_min:10:x_max;
elseif rem(x_max,50) == 0
    xticks_new = x_min:50:x_max;
elseif rem(x_max,40) == 0
    xticks_new = x_min:40:x_max;
elseif rem(x_max,30) == 0
    xticks_new = x_min:30:x_max;
elseif rem(x_max,20) == 0
    xticks_new = x_min:20:x_max;
elseif rem(x_max,10) == 0
    xticks_new = x_min:10:x_max;
elseif rem(x_max,5) == 0
    xticks_new = x_min:5:x_max;
elseif rem(x_max,4) == 0
    xticks_new = x_min:4:x_max;
elseif rem(x_max,2) == 0
    xticks_new = x_min:2:x_max;
else
    xticks_new = x_min:1:x_max;
end
xticks(xticks_new);
pos = get(fig,'Position');
pos_new = pos .* [1 1 0 0] + [0 0 WIDTH HEIGHT];
set(fig,'Position',pos_new);
centerFigure(fig);
set(ax,'FontSize',20);

end