function isInRange = adjustScale(scope,scopeChannel,YUNits_monitor,count)
%% Constants
VERT_SCALE = [...   % Vertical scale
    2e-3,...    % 1 mV/div
    5e-3,...    % 5 mV/div
    10e-3,... 	% 10 mV/div
    20e-3,...   % 20 mV/div
    50e-3,...   % 50 mV/div
    100e-3,...  % 100 mV/div
    200e-3,...  % 200 mV/div
    500e-3,... 	% 500 mV/div
    1,...       % 1 V/div
    2,...       % 2 V/div
    5];     	% 5 V/div
NUM_OF_SCALE = 11;
DIVS = 4;
MAX_COUNT = 2;

%% Variables
vertRange = VERT_SCALE * DIVS;
isInRange = false;
switch scopeChannel
    case 'CH1'
        unit = 'V';
    case 'CH2'
        unit = 'uA';
end
%% Function
% Adjust vertical scale
%     fprintf('Checking %s scale...',scopeChannel);
fprintf('checking...');
YUNits_min = min(YUNits_monitor);  % min
YUNits_max = max(YUNits_monitor);  % max

fprintf(scope,'TRIGger:MAIn:LEVel?');
triggeLevel = str2num(fscanf(scope)); %#ok<*ST2NM> 
if triggeLevel ~= 0
    if YUNits_min > -vertRange(1) && YUNits_max < vertRange(1)
        scale = VERT_SCALE(1);   % 5 mV/div vertical scale
        count(1) = count(1) + 1;
    elseif YUNits_min > -vertRange(2) && YUNits_max < vertRange(2)
        scale = VERT_SCALE(2);   % 10 mV/div vertical scale
        count(2) = count(2) + 1;
    elseif YUNits_min > -vertRange(3) && YUNits_max < vertRange(3)
        scale = VERT_SCALE(3);   % 20 mV/div vertical scale
        count(3) = count(3) + 1;
    elseif YUNits_min > -vertRange(4) && YUNits_max < vertRange(4)
        scale = VERT_SCALE(4);   % 50 mV/div vertical scale
        count(4) = count(4) + 1;
    elseif YUNits_min > -vertRange(5) && YUNits_max < vertRange(5)
        scale = VERT_SCALE(5);  % 100 mV/div vertical scale
        count(5) = count(5) + 1;
    elseif YUNits_min > -vertRange(6) && YUNits_max < vertRange(6)
        scale = VERT_SCALE(6);  % 200 mV/div vertical scale
        count(6) = count(6) + 1;
    elseif YUNits_min > -vertRange(7) && YUNits_max < vertRange(7)
        scale = VERT_SCALE(7);  % 500 mV/div vertical scale
        count(7) = count(7) + 1;
    elseif YUNits_min > -vertRange(8) && YUNits_max < vertRange(8)
        scale = VERT_SCALE(8);  % 1 V/div vertical scale
        count(8) = count(8) + 1;
    elseif YUNits_min > -vertRange(9) && YUNits_max < vertRange(9)
        scale = VERT_SCALE(9);  % 2 V/div vertical scale
        count(9) = count(9) + 1;
    elseif YUNits_min > -vertRange(10) && YUNits_max < vertRange(10)
        scale = VERT_SCALE(10);  % 5 V/div vertical scale
        count(10) = count(10) + 1;
    elseif YUNits_min > -vertRange(11) && YUNits_max < vertRange(11)
        scale = VERT_SCALE(11);  % 5 V/div vertical scale
        count(11) = count(11) + 1;
    end
%     for scaleNum = 1:NUM_OF_SCALE
%         rangeMin = -vertRange(scaleNum);
%         rangeMax = vertRange(scaleNum);
%         if YUNits_min > rangeMin && YUNits_max < rangeMax
%             scale = VERT_SCALE(scaleNum);   % 5 mV/div vertical scale
%             count(scaleNum) = count(scaleNum) + scaleNum;
%         else
%             break;
%         end
%     end
    if any(count >= MAX_COUNT)
        isInRange = true;
    end
    fprintf(scope,'ACQuire:STAte RUN');
    if ~isInRange
        %         fprintf('setting %s scale...',scopeChannel);
        scale_use = sprintf('%s:SCAle %.g',scopeChannel,scale);
        fprintf(scope,scale_use);
        fprintf('scaling to %.2e %s\n',scale,unit);
    else
        fprintf('OK.\n');
    end
else
    fprintf('OK.\n');
end

end