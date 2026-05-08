function isSet = setArbPattern(channel_arr,filePath_cell,varargin)
%% Variables
% numOfVar = length(varargin);
% if numOfVar > 0
%     pattern = varargin{1};
%     if numOfVar > 1
%         dischargeDelay = varargin{2};
%     end
% end

% Channel
numOfChannels = length(channel_arr);

% % Experiment
% fields = fieldnames(pattern);
% isTriphasic = contains2(fields,'A3');

% % Pattern
% amplitude1_nA = pattern.A1 * 1e3;
% amplitude2_nA = pattern.A2 * 1e3;
% phaseWidth1 = pattern.W1;
% phaseWidth2 = pattern.W2;
% interphaseDelay = pattern.Delay;
% hasInterphaseDelay = interphaseDelay > 0;
% afterInterphase1 = phaseWidth1 + interphaseDelay;
% pulseWidth = afterInterphase1 + phaseWidth2;
% if isTriphasic
%     amplitude3_nA = pattern.A3 * 1e3;
%     phaseWidth3 = pattern.W3;
%     pulseWidth = pulseWidth + interphaseDelay + phaseWidth3;
% end

isSet = false;
%% Set Arbitrary Pattern
fprintf('Setting arbitrary pattern...');
startTime = tic;
numOfFiles = length(filePath_cell);
for channel_idx = 1:numOfChannels
    channelNum = channel_arr(channel_idx);
    fprintf('%d...',channelNum);
    if numOfFiles > 1
        fileName = filePath_cell{channel_idx};
    else
        fileName = filePath_cell{1};
    end
    patternFile = fopen(fileName,'r');
    fgetl(patternFile);  % Skip 'variable'
    values = fscanf(patternFile,'%d');
    % display(values);
    fclose(patternFile);

    % Separate into amplitude and width
    values_len = length(values);
    len = values_len / 2;
    y_data = values(1:2:values_len);
    x_data = values(2:2:values_len);
    % data = table(x_data,y_data, ...
    %     'VariableNames',{'readX','readY'});
    % display(data);

    % Initialize
    xValues = NaN(values_len,1);
    yValues = NaN(values_len,1);

    % Build waveform
    x_prev = 0;
    for idx = 1:len
        y = y_data(idx);
        x = x_data(idx);

        % First point at current x
        xValues(2*idx-1) = x_prev;
        yValues(2*idx-1) = y;

        % Second point after width
        x_prev = x + x_prev;
        xValues(2*idx) = x_prev;
        yValues(2*idx) = y;
    end

    setTime = tic;
    isXWrong = true;
    isYWrong = true;
    count = 0;
    while isXWrong || isYWrong
        if count > 0
            PS_SetPatternType(1,channelNum,0);
        end
        PS_SetPatternType(1,channelNum,1);
        PS_LoadArbPattern(1,channelNum,fileName);
        try
            xValues_check = PS_GetArbPatternPointsX(1,channelNum)';
            yValues_check = PS_GetArbPatternPointsY(1,channelNum)';
        catch
            xValues_check = [];
            yValues_check = [];
            display(fileName);
            fprintf('Arbitrary pattern not loaded!\n');
        end
        isXWrong = ~isequal(xValues_check,xValues);

        x_expected_len = length(xValues);
        x_actual_len = length(xValues_check);
        x_len_max = max(x_expected_len, x_actual_len);
        x_expected_arr = NaN(x_len_max,1);
        x_actual_arr = NaN(x_len_max,1);
        x_expected_arr(1:x_expected_len) = xValues(:);
        x_actual_arr(1:x_actual_len) = xValues_check(:);
        x_table = table(x_expected_arr,x_actual_arr, ...
            'VariableNames',{'x_expected','x_actual'});

        isYWrong = ~isequal(yValues_check,yValues);
        y_expected_len = length(yValues);
        y_actual_len = length(yValues_check);
        y_len_max = max(y_expected_len, y_actual_len);
        y_expected_arr = NaN(y_len_max,1);
        y_actual_arr = NaN(y_len_max,1);
        y_expected_arr(1:y_expected_len) = yValues(:);
        y_actual_arr(1:y_actual_len) = yValues_check(:);
        y_table = table(y_expected_arr,y_actual_arr, ...
            'VariableNames',{'y_expected','y_actual'});

        if isXWrong || isYWrong
            count = count + 1;

            if isXWrong
                fprintf('\n');
                display(x_table);
            end

            if isYWrong
                fprintf('\n');
                display(y_table);
            end

            if isXWrong && isYWrong
                msg = sprintf('XY coordinates not matching!\n');
            elseif isXWrong
                msg = sprintf('X coordinates not matching!\n');
            elseif isYWrong
                msg = sprintf('Y coordinates not matching!\n');
            end
            % error(msg);
            fprintf(msg);
            % msgBox = msgbox(msg);
            % waitfor(msgBox);
        else
        end
        isSet = true;
        if isSet
            break;
        end
        % [endTime,unit] = getEndTime(setTime);
        % fprintf('(%.2f %s)...',endTime,unit);
    end
end

[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end