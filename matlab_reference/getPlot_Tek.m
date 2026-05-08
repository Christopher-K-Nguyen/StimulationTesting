function [File,fig] = getPlot_Tek(File)
%% Constants
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
THIRD_COLOR = '#EDB120';
FOURTH_COLOR = '#7E2F8E';
COLORS = {FIRST_COLOR,SECOND_COLOR,THIRD_COLOR,FOURTH_COLOR};
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;

% Buttons
BUTTON_YES = 'Yes';
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
BUTTON_NO = 'No';
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [1 60];

%% Variables
subject = File.Subject;
subject_fix = strrep(subject,'_','\_');
numOfChannels = File.Oscilloscope.NumberOfChannels;
numOfDevices = length(File.Oscilloscope);
lines = gobjects(1,numOfChannels);
channelSelect_cell = File.Oscilloscope.Channels;
channelName_cell = File.Oscilloscope.ChannelNames;
hasLegend = numOfChannels > 1;
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
name = File.Data(captureNum).Name;
name_fix = strrep(name,'_','\_');
time = File.Data(captureNum).Time;

% Monitor
if captureNum > 1
    prevCaptureNum = captureNum - 1;
else
    prevCaptureNum = 1;
end
Monitor = File.Data(prevCaptureNum).Monitor;
hasMonitor = isstruct(Monitor);

if ~hasMonitor
    monitorChannel_cell = [];
    File.Data(captureNum).Monitor.Channel = monitorChannel_cell;
    cursorName_cell = {};
    File.Data(captureNum).Monitor.Name = cursorName_cell;
    cursorTime_cell = [];
    File.Data(captureNum).Monitor.Time = cursorTime_cell;
else
    monitorChannel_cell = File.Data(prevCaptureNum).Monitor.Channel;
    cursorName_cell = File.Data(prevCaptureNum).Monitor.Name;
    cursorTime_cell = File.Data(prevCaptureNum).Monitor.Time;
end

% Screen
screen = get(0,'MonitorPositions');
screen1 = screen(1,3);
screen2 = screen(2,3);
if screen1 > screen2
    screenWidth = screen(1,3);
    screenHeight = screen(1,4);
    posX = screen(1,1);
    posY = screen(1,2);
else
    screenWidth = screen(2,3);
    screenHeight = screen(2,4);
    posX = screen(2,1);
    posY = screen(2,2);
end
figPosX = ceil((screenWidth - FIG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - FIG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Previous Settings
if captureNum > 1
    [needsMonitorChannel,needsCursor] = checkPreviousCursor(File);
else
    needsMonitorChannel = true;
    needsCursor = true;
end

%% Monitor
if needsMonitorChannel
    [File,~] = getMonitorChannel_Tek(File);
end

%% Cursor Time
if needsCursor
    [File,~] = getCursorTime_Tek(File);
end


%% Cursor Name
if needsCursor
    [File,~] = getCursorName_Tek(File);  
end

%% Access Voltage
% % Derivative
% data_diff = diff(data);
% time_diff = diff(data);
% derivative_raw = data_diff ./ time_diff;
% derivative = lowpass(derivative_raw,0.0001);
%
% % Peaks
% peakMax = max(derivative);
% peakPos_thresh = peakMax * 0.8;
% peakNeg_thresh = peakMax * 0.8;
%
% % Initialization
% accessVoltage1 = 0;
% accessVoltage2 = 0;
% drivingVoltage1 = 0;
% drivingVoltage2_plot = 0;
% isAccessDerivative1Good = false;
% isAccessDerivative2Good = false;
% isEndDerivative1Good = false;
% isEndDerivative2Good = false;
% accessDerivative1_idx = [];
% accessDerivative2_idx = [];
% endDerivative1_idx = [];
% endDerivative2_idx = [];
% accessVoltage1_idx = [];
% accessVoltage2_idx = [];
% endPhase1_idx = [];
% endPhase2_idx = [];
%
% % Indices
% fprintf('Getting voltage indices...');
% accessCheckTime = tic;
% while isempty(accessVoltage1_idx) || isempty(accessVoltage2_idx) ...
%         || isempty(endPhase1_idx) || isempty(endPhase2_idx)
%     [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
%     [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
%     switch amplitude1_sign
%         case -1
%             accessDerivative1_idx = min(peaksNeg_idx);
%             accessDerivative2_idx = max(peaksPos_idx);
%             endDerivative1_idx = min(peaksPos_idx);
%             endDerivative2_idx = max(peaksNeg_idx);
%         case 1
%             accessDerivative1_idx = min(peaksPos_idx);
%             accessDerivative2_idx = max(peaksNeg_idx);
%             endDerivative1_idx = min(peaksNeg_idx);
%             endDerivative2_idx = max(peaksPos_idx);
%     end
%
%     % Acess Voltage 1
%     if isempty(accessDerivative1_idx)...
%             || accessDerivative1_idx < time0_idx ...
%             || accessDerivative1_idx > accessDerivative1_check
%         accessDerivative1_idx = [];
%         switch amplitude1_sign
%             case -1
%                 peakNeg_thresh = peakNeg_thresh * 0.9;
%             case 1
%                 peakPos_thresh = peakPos_thresh * 0.9;
%         end
%     else
%         accessVoltage1_idx = accessDerivative1_idx;
%         isAccessDerivative1Good = true;
%     end
%
%     % Access Voltage 2
%     if isempty(accessDerivative2_idx) ...
%             || accessDerivative2_idx < potentialExcursion1_idx ...
%             || accessDerivative2_idx > accessDerivative2_check
%         accessDerivative2_idx = [];
%         switch amplitude1_sign
%             case -1
%                 peakPos_thresh = peakPos_thresh * 0.9;
%             case 1
%                 peakNeg_thresh = peakNeg_thresh * 0.9;
%         end
%     else
%         accessVoltage2_idx = accessDerivative2_idx;
%         isAccessDerivative2Good = true;
%     end
%
%     % End Phase 1
%     if isempty(endDerivative1_idx)...
%             || endDerivative1_idx < accessDerivative1_check ...
%             || endDerivative1_idx > potentialExcursion1_idx ...
%             || endDerivative1_idx < endPhase1_idx_old * 0.9
%         endDerivative1_idx = [];
%         switch amplitude1_sign
%             case -1
%                 peakNeg_thresh = peakNeg_thresh * 0.9;
%             case 1
%                 peakPos_thresh = peakPos_thresh * 0.9;
%         end
%     else
%         endPhase1_idx = endDerivative1_idx;
%         isEndDerivative1Good = true;
%     end
%
%     % End Phase 2
%     if isempty(endDerivative2_idx) ...
%             || endDerivative2_idx > potentialExcursion2_idx ...
%             || endDerivative2_idx < accessDerivative2_check ...
%             || endDerivative2_idx < endPhase2_idx_old * 0.9
%         endDerivative2_idx = [];
%         switch amplitude1_sign
%             case -1
%                 peakPos_thresh = peakPos_thresh * 0.9;
%             case 1
%                 peakNeg_thresh = peakNeg_thresh * 0.9;
%         end
%     else
%         endPhase2_idx = endDerivative2_idx;
%         isEndDerivative2Good = true;
%     end
%
%     if toc(accessCheckTime) > 1
%         if isempty(accessVoltage1_idx)
%             accessVoltage1_idx = accessVoltage1_idx_old;
%             isAccessDerivative1Good = false;
%         end
%         if isempty(accessVoltage2_idx)
%             accessVoltage2_idx = accessVoltage2_idx_old;
%             isAccessDerivative2Good = false;
%         end
%         if isempty(endDerivative1_idx)
%             endPhase1_idx = endPhase1_idx_old;
%             isEndDerivative1Good = false;
%         end
%         if isempty(endDerivative2_idx)
%             endPhase2_idx = endPhase2_idx_old;
%             isEndDerivative2Good = false;
%         end
%     end
% end
%
% % Shift
% if isAccessDerivative1Good
%     accessVoltage1_idx = accessVoltage1_idx + idx_shift + idx_shift_more;
%     endInterphase_shift_idx = idx_shift_more + 16;
% end
% if isAccessDerivative2Good
%     accessVoltage2_idx = accessVoltage2_idx + idx_shift + idx_shift_more;
% end
% if isEndDerivative1Good
%     endPhase1_idx = endPhase1_idx + idx_shift + idx_shift_more;
% end
% if isEndDerivative2Good
%     endPhase2_idx = endPhase2_idx + idx_shift;
% end
% [endTime,unit] = getEndTime(startTime);
% fprintf('OK (%.2f %s)\n',endTime,unit);

%% Plot
fprintf('Creating plot...\n');
fig = figure(captureNum);
clf;
delete(findall(fig,'type','annotation'));
ax = gca;
set(ax,'XColor','k');
for channel_idx = 1:numOfChannels
    channel = channelSelect_cell{channel_idx};
    fprintf('\tPlotting...');
    channelName = channelName_cell{channel_idx};
    channelUnit_raw = extractBetween(channelName,'(',')');
    channelUnit = channelUnit_raw{1};
    data = File.Data(captureNum).(channel);
    color = COLORS{channel_idx};
    set(ax,'YColor','k');
    if contains2(channel,'curr')
        yyaxis right;
        %         channelName_fix = strrep(channelName,'uA','\muA');
        channelName_fix = sprintf('Current Density (A/cm^{2})');
        currentDensity = File.Data(captureNum).CurrentDensity;
        lines(channel_idx) = plot(time,currentDensity, ...
            'Color',color, ...
            'LineStyle','-', ...
            'LineWidth',1, ...
            'DisplayName',channelName_fix);
        ylabel(channelName_fix,...
            'Color',color,...
            'Rotation',-90,....
            'HorizontalAlignment','center',...
            'VerticalAlignment','bottom');
    else
        yyaxis left;
        lines(channel_idx) = plot(time,data, ...
            'Color',color, ...
            'LineStyle','-', ...
            'LineWidth',1, ...
            'DisplayName',channelName);
    end
    fprintf('%s\n',channel);
    hold on;
        
    % Annotations
    if contains2(channel,monitorChannel_cell)
        % Max
        [data_max,max_idx] = max(data);
        File.Data(captureNum).Monitor.Maximum = data_max; 
        % Min
        [data_min,min_idx] = min(data);
        File.Data(captureNum).Monitor.Minimum = data_min;
        % Horizontal bars
        yLine = [data_max data_min];
        maxLabel = sprintf('%.3f %s',data_max,channelUnit);
        minLabel = sprintf('%.3f %s',data_min,channelUnit);
        yLineLabel = {maxLabel,minLabel};
        if contains2(monitorChannel_cell,'CH2')
            barPos = 'left';
        else
            barPos = 'right';
        end
        yline(yLine,':',yLineLabel, ...
            'LabelHorizontalAlignment',barPos, ...
            'LabelVerticalAlignment','middle', ...
            'HandleVisibility','off');
        fprintf('\t\tMin: %.3f %s\n',data_min,channelUnit);
        fprintf('\t\tMax: %.3f %s\n',data_max,channelUnit);

        % Cursors
        cursorValue_arr = zeros(numOfCursor,1);
        textLabel = cell(1,numOfCursor);
        for cursor_idx = 1:numOfCursor
            cursorName = cursorName_cell{cursor_idx};
            cursorTime = cursorTime_cell{cursor_idx};
            cursorTime_idx = find(time >= cursorTime,1);
            cursorValue = data(cursorTime_idx);
            cursorValue_arr(cursor_idx) = cursorValue;
            textLabel{cursor_idx} = sprintf('%s = %.3f %s', ...
                cursorName,cursorValue,channelUnit);
            fprintf('\t\tCursor %s at %g us: %.3f %s\n', ...
                cursorName,cursorTime,cursorValue,channelUnit);
        end
        File.Data(captureNum).Monitor.Value = cursorValue_arr;
        scatter(cursorTime_cell,cursorValue_arr,'filled','x', ...
            'MarkerEdgeColor',[0.5 0.5 0.5], ...
            'LineWidth',2, ...
            'SizeData',70, ...
            'HandleVisibility','off');
        if min_idx < max_idx
            cursorPos = 'top';
        else
            cursorPos = 'bottom';
        end
        text(cursorTime_cell,cursorValue_arr,textLabel, ...
            'HorizontalAlignment','left', ...
            'VerticalAlignment',cursorPos, ...
            'FontSize',14);
    end
end

fprintf('Finalizing plot for %s...',name);
if hasLegend
    legend('Location','best','FontSize',14);
    % lines,channelName_cell
end
switch numOfChannels
    case 1
        ylabel(channelName);
    case 2
        yyaxis left;
        ylabel(channelName);
    otherwise
        yyaxis left;
        ylabel('Voltage (V)');
end

% Center zero
% Fix Left Axis

yyaxis left;
ylim('tickaligned');
yLlimL = ylim;
yLimL_big = max(abs(yLlimL));
yLimL_min = -yLimL_big;
yLimL_max = yLimL_big;
ylim([yLimL_min yLimL_max]);
yTicksL_check = yticks;
yTicksL_diff_raw = mean(abs(diff(yTicksL_check)));
yTicksL_diff = round(yTicksL_diff_raw,2,'significant');
yTicksL = yLimL_min:yTicksL_diff:yLimL_max;
if ~ismember(0,yTicksL)
    yTicksL = [yLimL_min 0 yLimL_max];
end
yticks(yTicksL);
yTicksL = yticks;
yTicksL_max = max(yTicksL);
if yTicksL_max ~= yLimL_max
    yLimL_max_new = round(yLimL_max + yTicksL_diff,2,'significant');
    yLimL_min_new = -yLimL_max_new;
    yLimL_new = [yLimL_min_new yLimL_max_new];
    yTicksL_new = yLimL_min_new:yTicksL_diff:yLimL_max_new;
    if ~ismember(0,yTicksL_new)
        yTicksL_new = [yLimL_min_new 0 yLimL_max_new];
    end
    ylim(yLimL_new);
    yticks(yTicksL_new);
end

% Fix Right Axis
yyaxis right;
ylim('tickaligned');
yLimR = ylim;
yLimR_big = max(abs(yLimR));
yLimR_min = -yLimR_big;
yLimR_max = yLimR_big;
ylim([yLimR_min yLimR_max]);
yTicksR_check = yticks;
yTicksR_diff_raw = mean(abs(diff(yTicksR_check)));
yTicksR_diff = round(yTicksR_diff_raw,2,'significant');
yTicksR = yLimR_min:yTicksR_diff:yLimR_max;
if ~ismember(0,yTicksR)
    yTicksR = [yLimR_min 0 yLimR_max];
end
yticks(yTicksR);
yTicksR = yticks;
yTicksR_max = max(yTicksR);
if yTicksR_max ~= yLimR_max
    yLimR_max_new = round(yLimR_max + yTicksR_diff,2,'significant');
    yLimR_min_new = -yLimR_max_new;
    yLimR_new = [yLimR_min_new yLimR_max_new];
    yTicksR_new = yLimR_min_new:yTicksR_diff:yLimR_max_new;
    if ~ismember(0,yTicksR_new)
        yTicksR_new = [yLimR_min_new 0 yLimR_max_new];
    end
    ylim(yLimR_new);
    yticks(yTicksR_new);
end

title(subject_fix);
subtitle(name_fix);
xlabel('Time (\mus)');
xMin = round(min(time),1,'significant');
xMax = round(max(time),1,'significant');
xlim([xMin xMax]);
set(gcf,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
set(ax,'FontSize',20);
box on;
drawnow;
fprintf('OK\n');

end