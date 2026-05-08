function [File,fig] = getPlot_new_Tek(File)
%% Constants
COLORS = { ...
    '#D95319', ...
	'#808080', ...
    '#EDB120', ...
    '#0072BD', ...
    '#7E2F8E', ...
    '#77AC30', ...
    '#4DBEEE', ...
    '#A2142F'};
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
numOfDevices = length(File.Oscilloscope);
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
name = File.Data(captureNum).Name;
name_fix = strrep(name,'_','\_');
title_use = [subject_fix '\_' name_fix];
time = File.Data(captureNum).Time;
surfaceArea = File.Data(captureNum).SurfaceArea;
markerType_cell = {'+','x','*','pentagram','hexagram'};
% cursorColor_cell = COLORS;
numOfChannels_arr = zeros(1,numOfDevices);
totalChannels = 0;
for deviceNum = 1:numOfDevices
    % Total number of channels
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    numOfChannels_arr(deviceNum) = numOfChannels;
    totalChannels = totalChannels + numOfChannels;
end
lines = gobjects(1,totalChannels);

% Screen
screen = get(0,'MonitorPositions');
[numOfScreens,~] = size(screen);
switch numOfScreens
    case 1
        screenWidth = screen(1,3);
        screenHeight = screen(1,4);
        posX = screen(1,1);
        posY = screen(1,2);
    case 2
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
end
figPosX = ceil((screenWidth - FIG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - FIG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Previous Settings
confirmValues = false;
promptValues = { ...
    'Enter current amplitude (\muA):', ...
    'Enter phase width (\mus):', ...
    'Enter maximum potential excursion time (\mus):'};
while ~confirmValues
    try
        amplitude_prev = File.Data(captureNum).Amplitude;
        phaseWidth_prev = File.Data(captureNum).PhaseWidth;
        cursorTime_prev = File.Data(captureNum).Monitor.Time;
        if ~isempty(cursorTime_prev)
            defaultAmplitude_char = num2str(amplitude_prev);
            defaultPhaseWidth_char = num2str(phaseWidth_prev);
            defaultCursorTime_char = num2str(cursorTime_prev);
            defaultValues = { ...
                defaultAmplitude_char, ...
                defaultPhaseWidth_char, ...
                defaultCursorTime_char};
        end
    catch
        if captureNum > 1
            amplitude_prev = File.Data(captureNum-1).Amplitude;
            phaseWidth_prev = File.Data(captureNum-1).PhaseWidth;
            cursorTime_prev = File.Data(captureNum-1).Monitor.Time;
            defaultAmplitude_char = num2str(amplitude_prev);
            defaultPhaseWidth_char = num2str(phaseWidth_prev);
            defaultCursorTime_char = num2str(cursorTime_prev);
            defaultValues = { ...
                defaultAmplitude_char, ...
                defaultPhaseWidth_char, ...
                defaultCursorTime_char};
        else
            defaultValues = {'','',''};
        end
    end
    inputValues = inputdlg( ...
            promptValues, ...
            'Values', ...
            DIMS, ...
            defaultValues, ...
            opts);
    if isempty(inputValues)
        continue;
    end
    amplitude_raw = inputValues{1};
    amplitude = str2double(amplitude_raw);
    phaseWidth_raw = inputValues{2};
    phaseWidth = str2double(phaseWidth_raw);
    cursorTime_raw = inputValues{3};
    if contains2(cursorTime_raw,{',',' '})
        if contains2(cursorTime_raw,',')
            cursorTime_erase = erase(cursorTime_raw,' ');
            cursorTime_fix = convertCharsToStrings(cursorTime_erase);
            cursorTime_cell = strsplit(cursorTime_fix,',');
        else
            cursorTime_erase = strrep(cursorTime_raw,'  ',' ');
            cursorTime_fix = convertCharsToStrings(cursorTime_erase);
            cursorTime_cell = strsplit(cursorTime_fix,' ');
        end
        cursorTime_arr = str2double(cursorTime_cell);
    else
        cursorTime_arr = str2double(cursorTime_raw);
    end
    defaultValues = {amplitude_raw,cursorTime_raw};

    opts.Default = BUTTON_CONFIRM;
    amplitude_use = sprintf('Current amplitude: {\\bf%g \\muA}',amplitude);
    phaseWidth_use = sprintf('PhaseWidth: {\\bf%g \\mus}',phaseWidth);
    if contains2(cursorTime_raw,{',',' '})
        cursorTime_fix = strjoin(cursorTime_cell,', ');
        cursorTime_use = sprintf('Maximum Potential Excursion Time: {\\bf%s \\mus}',cursorTime_fix);
    else
        cursorTime_use = sprintf('Maximum Potential Excursion Time: {\\bf%g \\mus}',cursorTime_arr);
    end
    questPrompt = {amplitude_use,phaseWidth_use,cursorTime_use};
    questValues = questdlg(questPrompt, ...
        'Confirm Values', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ...
        opts);
    switch questValues
        case BUTTON_CONFIRM
            confirmValues = true;
        case BUTTON_TRY
            continue;
        case BUTTON_CANCEL
            amplitude = amplitude_prev;
            phaseWidth = phaseWidth_prev;
            cursorTime_arr = cursorTime_prev;
            confirmValues = true;
    end
end


% if numOfDevices > 1
%     numOfCursor = 2;
% else
%     if numOfChannels > 2
%         numOfCursor = 2;
%     else
%         numOfCursor = 1;
%     end
% end
numOfCursor = length(cursorTime_arr);
File.Data(captureNum).Amplitude = amplitude;
[chargePhase,chargeInjection] = getCharge(amplitude,phaseWidth,surfaceArea);
subtitle_use = sprintf( ...
    '{\\itI}_{mag} = %g \\muA, {\\itt}_{ph} = %g \\mus, {\\itQ}_{ph} = %g nC/ph, {\\itQ}_{inj} = %g mC/cm^2', ...
    amplitude,phaseWidth,chargePhase,chargeInjection);
File.Data(captureNum).PhaseWidth = phaseWidth;
File.Data(captureNum).ChargePhase = chargePhase;
File.Data(captureNum).ChargeInjection = chargeInjection;
File.Data(captureNum).Monitor.Time = cursorTime_arr;
cursorValue_mat = zeros(totalChannels,numOfCursor);
max_arr = zeros(numOfCursor,1);
min_arr = zeros(numOfCursor,1);
if numOfCursor > 1
    cursorTime_idx = zeros(1,numOfCursor);
    for cursor_idx = 1:numOfCursor
        cursorTime = cursorTime_arr(cursor_idx);
        cursorTime_idx(cursor_idx) = find(time >= cursorTime,1);
    end
else
    cursorTime_idx = find(time >= cursorTime_arr,1);
end
cursorLabel_cell = cell(totalChannels,numOfCursor);
cursorColor_cell = cell(totalChannels,numOfCursor);

%% Plot
fprintf('Creating plot...\n');
fig = figure(captureNum);
clf;
delete(findall(fig,'type','annotation'));
figure(fig);
ax = gca;
set(ax,'XColor','k');
line_idx = 0;
% cursor_idx = 0;

channelCount = 0;
for deviceNum = 1:numOfDevices
%     switch deviceNum
%         case 1
%             lineSize = 2;
%         case 2
%             lineSize = 1;
%     end
    numOfChannels = numOfChannels_arr(deviceNum);
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    hasLegend = numOfChannels > 1;
    for channel_idx = 1:numOfChannels
        channelCount = channelCount + 1;
        line_idx = line_idx + 1;
        channel = channelSelect_cell{channel_idx};
        channelName = channelName_cell{channel_idx};
        channelField = channel;
        channelUnit_raw = extractBetween(channelName,'(',')');
        channelUnit = channelUnit_raw{1};
%         if numOfDevices == 2 && deviceNum == 1
%             switch channel_idx
%                 case 1
%                     channelField = 'Voltage';
% 
% %                     lineStyle = '-';
%                 case 2
%                     channelField = 'Current';
% %                     lineStyle = '--';
%             end
            lineStyle = '-';
            marker = 'none';
        % else
        %     channelField = channelSelect_cell{channel_idx};
        %     lineStyle = 'none';
        %     lineWidth = 0.5;
        %     marker = 'o';
        % end
        if contains2(channelName,{'volt','curr'})
            lineWidth = 2.5;
        else
            lineWidth = 1.5;
        end
        data = File.Data(captureNum).(channelField);
        if ~isempty(data)
            fprintf('\tPlotting...');
            startTime = tic;
            color = COLORS{line_idx};
            lineColor = color;
            set(ax,'YColor','k');
            if contains2(channelName,'curr')
                yyaxis right;
                %         channelName_fix = strrep(channelName,'uA','\muA');
                channelName_fix = sprintf('Current Density (A/cm^{2})');
                currentDensity = File.Data(captureNum).CurrentDensity;
                lines(line_idx) = plot(time,currentDensity, ...
                    'Color',lineColor, ...
                    'LineStyle',lineStyle, ...
                    'LineWidth',lineWidth, ...
                    'DisplayName',channelName_fix);
                ylabel2(channelName_fix,lineColor);
            else
                yyaxis left;
                if contains2(lineStyle,'none')
                    lineColor = 'none';
                end
                lines(line_idx) = plot(time,data, ...
                    'Color',lineColor, ...
                    'LineStyle',lineStyle, ...
                    'LineWidth',lineWidth, ...
                    'Marker',marker, ...
                    'MarkerSize',2, ...
                    'MarkerFaceColor',color, ...
                    'MarkerEdgeColor','none', ...
                    'DisplayName',channelName);
            end
            [endTime,unit] = getEndTime(startTime);
            fprintf('%s (%.2f %s)\n',channel,endTime,unit);
            hold on;

            % Annotations
            % if (contains2(channel,'ch1') && numOfDevices > 1) || ...
            %         (contains2(channelName,'act') && numOfDevices == 1)
            if ~contains2(channelName,'curr')
                % cursor_idx = cursor_idx + 1;
                cursorValue_arr = data(cursorTime_idx);
                cursorValue_mat(channelCount,:) = cursorValue_arr(:);
                for cursor_idx = 1:numOfCursor
                    cursorValue = cursorValue_arr(cursor_idx);
                    isColorGood = false;
                    shade = [1 1 1] * 0.99;
                    % colorShift = [0.05 0.05 0.05];
                    color_rgb = hex2rgb(color);
                    while ~isColorGood
                        newColor = color_rgb - shade;
                        if any(newColor >= 1 | newColor <= 0)
                            % if any(shade < 1e-3)
                        %         color_rgb = color_rgb + colorShift;
                        %         colorShift = colorShift * 1.1;
                        %     else
                                shade = shade * 0.9;
                        %     end
                        else
                            isColorGood = true;
                        end
                    end
                    cursorColor_cell{channelCount,cursor_idx} = newColor;
                    switch sign(cursorValue)
                        case -1
                            label_sign = 'c';
                        case 1
                            label_sign = 'a';
                    end
                    cursorLabel_cell{channelCount,cursor_idx} = sprintf('{\\itE}_{m%s} = %.3f %s', ...
                        label_sign,cursorValue,channelUnit);
                    fprintf('\t\tEm%s at %g us: %.3f %s\n', ...
                        label_sign,cursorTime,cursorValue,channelUnit);
                end
                max_value = max(data);
                min_value = min(data);
                max_arr(cursor_idx) = max_value;
                min_arr(cursor_idx) = min_value;
                File.Data(captureNum).Monitor.Value = cursorValue_mat;
                File.Data(captureNum).Monitor.Maximum = max_arr;
                File.Data(captureNum).Monitor.Minimum = min_arr;                
            end
        end
    end
end

fprintf('Finalizing plot for "%s"...',name);
startTime = tic;
figure(fig);
% 'MarkerEdgeColor',[0.5 0.5 0.5], ...
channelCount = 0;
for deviceNum = 1:numOfDevices
    numOfChannels = numOfChannels_arr(deviceNum);
    for channel_idx = 1:numOfChannels
        channelCount = channelCount + 1;
        channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
        channelName = channelName_cell{channel_idx};
        if ~contains2(channelName,'curr')
            for cursor_idx = 1:numOfCursor
                markerType = '+';
                markerColor = cursorColor_cell{channelCount,cursor_idx};
                xValue = cursorTime_arr(cursor_idx);
                yValue = cursorValue_mat(cursor_idx);
                scatter(xValue,yValue,'filled',markerType, ...
                    'MarkerEdgeColor',markerColor, ...
                    'LineWidth',1, ...
                    'SizeData',90, ...
                    'HandleVisibility','off');

                % switch cursor_idx
                % case 1
                horizPos = 'left';
                % case 2
                %         horizPos = 'right';
                %     case 3
                %         horizPos = 'left';
                % end
                % horizPos = 'center';
                switch sign(yValue)
                    case 1
                        vertPos = 'bottom';
                    case -1
                        vertPos = 'top';
                end

                cursorTime_value = cursorTime_arr(cursor_idx);
                cursorValue = cursorValue_mat(channelCount,cursor_idx);
                textLabel_value = cursorLabel_cell{channelCount,cursor_idx};
                text(cursorTime_value,cursorValue,textLabel_value, ...
                    'Color',markerColor, ...
                    'HorizontalAlignment',horizPos, ...
                    'VerticalAlignment',vertPos, ...
                    'FontSize',10, ...
                    'HandleVisibility','off');
            end
        end
    end
end

if hasLegend
    legend('Location','southeast','FontSize',10);
    % lines,channelName_cell
end

% Center zero
% Fix Left Axis
yyaxis left;
ylabel('Voltage (V)');
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

title(title_use);
sub = subtitle(subtitle_use);
xlabel('Time (\mus)');
xMin = round(min(time),2,'significant');
xMax = round(max(time),2,'significant');
xlim([xMin xMax]);
set(gcf,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
set(ax,'FontSize',20);
sub.FontSize = 16;
box on;
drawnow;
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end