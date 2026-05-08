function [File,isQuit] = getConfiguration(File)
%% Constants
LISTDLG_SIZE = [180 160];
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_QUIT = 'Quit';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';

%% Variables
deviceType = File.Parameters.Device;
expType = File.Test.Experiment;
isVT = contains2(expType,'VT');
% isPulsing = contains(expType,{'SP','LP'});
testChannel_arr = File.Parameters.Channels.Test;
numOfTestChannels = length(testChannel_arr);
isQuit = false;

% Number of Poles
config_cell = { ...
    'monopolar', ...
    'bipolar', ...
    'bipolar (adjacent only)', ...
    'partial bipolar', ...
    'partial bipolar (adjacent only)', ...
    'tripolar', ...
    'tripolar (adjacent only)', ...
    'tripolar (flanking only)', ...
    'partial tripolar', ...
    'partial tripolar (adjacent only)', ...
    'partial tripolar (flanking only)', ...
    'common ground'};
    % 'all-polar'};

configID_cell = { ...
    'MP', ...
    'BP', ...
    'BP_ADJ', ...
    'PBP', ...
    'PBP_ADJ', ...
    'TP', ...
    'TP_ADJ', ...
    'TP_OPP', ...
    'PTP', ...
    'PTP_ADJ', ...
    'PTP_OPP', ...
    'CG'};
    % 'AP'};

numOfPoles_arr = [ ...
    1, ...
    2, ...
    2, ...
    2.5, ...
    2.5, ...
    3, ...
    3, ...
    3, ...
    3.5, ...
    3.5, ...
    3.5, ...
    16];
    % -16];

%% Function
if ~contains2(deviceType,{'oth','test'})
    % if ~isempty(varargin)
    %     var1 = varargin{1};
    %     className = class(var1);
    %     switch className
    %         case 'char'
    %             if length(var1) > 3
    %                 if contains2(var1,'mono')
    %                     config_idx = 1;
    %                 elseif contains2(var1,'bipol') && ~contains2(var1,{'pbp','part'})
    %                     config_idx = 2;
    %                 elseif contains2(var1,'pbp')
    %                     config_idx = 2;
    %                 elseif contains2(var1,'tri') && ~contains2(var1,{'ptp','part'})
    %                     config_idx = 3;
    %                 elseif contains2(var1,'ptp')
    %                     config_idx = 4;
    %                 elseif contains2(var1,{'com','gnd','gro'})
    %                     config_idx = 5;
    %                 elseif contains2(var1,'all')
    %                     config_idx = 6;
    %                 end
    %                 configID = configID_cell{config_idx};
    %                 config = config_cell{config_idx};
    %                 numOfPoles = numOfPoles_arr(config_idx);
    %             else
    %                 configID = lower(var1);
    %                 config_tf = containsi(configID_cell,configID);
    %                 config = config_cell{config_tf};
    %                 numOfPoles = numOfPoles_arr(config_tf);
    %             end
    %         case 'double'
    %             numOfPoles = var1;
    %             switch numOfPoles
    %                 case {1,2,3}
    %                     config_idx = find(numOfPoles_arr == numOfPoles,1);
    %                 case 3.5
    %                     config_idx = 4;
    %                 case 16
    %                     config_idx = 5;
    %                 case -16
    %                     config_idx = 6;
    %             end
    %             config = config_cell{config_idx};
    %             configID = configID_cell{config_idx};
    %     end
    % end

    %% Function
    fprintf('Select configuration...');

    confirmConfig = false;
    while ~confirmConfig
        % Initial Reference
        [config_idx,~] = listdlg( ...
            'PromptString','Select configuration', ...
            'ListString',config_cell, ...
            'SelectionMode','single', ...
            'InitialValue',1, ...
            'ListSize',LISTDLG_SIZE);
        if ~isempty(config_idx)
            configID = configID_cell{config_idx};
            config = config_cell{config_idx};
            configID_use = strrep(configID,'_','\_');
            numOfPoles = numOfPoles_arr(config_idx);
        else
            isQuit = true;
            break;
        end

        % Confirm
        questInput = sprintf('Configuration: {\\bf%s (%s)} ',config,configID_use);
        questPotential = questdlg( ...
            questInput, ...
            'Confirm Configuration', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
            opts);
        switch questPotential
            case BUTTON_CONFIRM
                confirmConfig = true;
                fprintf('%s\n',config);
            case BUTTON_TRY
                continue;
            case BUTTON_QUIT
                isQuit = true;
                break;
        end
    end
else
    config_idx = 1;
    configID = configID_cell{config_idx};
    config = config_cell{config_idx};
    numOfPoles = numOfPoles_arr(config_idx);
end
if isQuit
    return;
end

%% Store
File.Parameters.Configuration.ID = configID;
File.Parameters.Configuration.Type = config;
File.Parameters.Configuration.Number = numOfPoles;
isMP = contains2(configID,'MP');
isBP = contains2(configID,'BP') && ~contains2(configID,'PBP');
isPBP = contains2(configID,'PBP');
isTP = contains2(configID,'TP') && ~contains2(configID,'PTP');
isPTP = contains2(configID,'PTP');
isCG = contains2(configID,'CG');
isAdj = contains2(configID,'ADJ');
isOpp = contains2(configID,'OPP');
isAny = isAdj || isOpp;

% Bipolar Mapping

if isBP
    mappingBP_mat = zeros(numOfTestChannels,numOfTestChannels);
    % testChannel_cell = strsplit(num2str(testChannel_arr));
    % stimChannel_cell = testChannel_cell;
    % retChannel_cell = testChannel_cell;
    % for channel_config_idx = 1:numOfTestChannels
    %     channelNum_char = testChannel_cell{channel_config_idx};
    %     stimChannel_cell{channel_config_idx} = ['Active ' channelNum_char];
    %     retChannel_cell{channel_config_idx} = ['Return ' channelNum_char];
    % end
    % mappingBP1_table = array2table(mappingBP_mat, ...
    %     'VariableNames',retChannel_cell, ...
    %     'RowNames',stimChannel_cell);
    % mappingBP2_table = array2table(mappingBP_mat, ...
    %     'VariableNames',stimChannel_cell, ...
    %     'RowNames',retChannel_cell);
    File.Parameters.Mapping.Bipolar = [];
    File.Parameters.Mapping.Bipolar.ActiveRow = mappingBP_mat;
    File.Parameters.Mapping.Bipolar.ActiveColumn = mappingBP_mat;
end

% List of Channel Groups
if ~isMP
    if isCG
        channelGroup_mat = zeros(numOfTestChannels,numOfTestChannels);
        for testChannel_idx = 1:numOfTestChannels
            channelReturn_arr = testChannel_arr;
            channelReturn_arr(testChannel_idx) = [];
            channelNum = testChannel_arr(testChannel_idx);
            channelGroup_arr = [channelNum channelReturn_arr];
            channelGroup_mat(testChannel_idx,:) = channelGroup_arr;
        end
    else
        numOfReturns = floor(numOfPoles) - 1;
        channelMapping = File.Parameters.Mapping.Channel;
        channelGroup_mat = [];
        exclude_row = [];
        for testChannel_idx = 1:numOfTestChannels
            channelNum = testChannel_arr(testChannel_idx);
            fprintf('\t');
            if isAny && ~isAdj
                tag = 'any';
            else
                tag = 'adj';
            end
            channelReturn_arr = getNeighbor(channelMapping,channelNum,tag);
            channelNotTest_tf = ~ismember(channelReturn_arr,testChannel_arr);
            if any(channelNotTest_tf)
                channelReturn_arr(channelNotTest_tf) = [];
            end
            channelReturn_mat = nchoosek(channelReturn_arr,numOfReturns);
            [numOfRows,~] = size(channelReturn_mat);
            if numOfRows > 1
                for rowNum = 1:numOfRows
                    channelReturn_row = sort(channelReturn_mat(rowNum,1:numOfReturns));
                    if isOpp && numOfReturns > 1
                        oppTime = tic;
                        return1 = channelReturn_mat(rowNum,1);
                        return2 = channelReturn_mat(rowNum,2);
                        [return1_x,return1_y] = find(channelMapping == return1);
                        [return2_x,return2_y] = find(channelMapping == return2);
                        return1_xy = [return1_x,return1_y];
                        return2_xy = [return2_x,return2_y];
                        distance_check = norm(return1_xy - return2_xy);
                        if ~(distance_check == 2 || distance_check == 2 * sqrt(2))
                            exclude_row_alloc = [exclude_row;rowNum];
                            exclude_row = exclude_row_alloc;
                        else
                            channelGroup_arr = [channelNum channelReturn_row];
                            channelGroup_mat_alloc = [channelGroup_mat;channelGroup_arr];
                            channelGroup_mat = channelGroup_mat_alloc;
                            channelReturn_cell = string(num2cell(channelReturn_row));
                            channelReturn_list = strjoin(channelReturn_cell,',');
                            [endTime,unit] = getEndTime(oppTime);
                            fprintf('\t\tChannel %d + %s (%.2f %s)\n',channelNum,channelReturn_list,endTime,unit);
                        end
                    else
                        channelGroup_arr = [channelNum channelReturn_row];
                        channelGroup_mat_alloc = [channelGroup_mat;channelGroup_arr];
                        channelGroup_mat = channelGroup_mat_alloc;
                    end
                end
            end
        end
    end
    if isAny
        channelGroup_mat(exclude_row,:) = [];
    end
    [numOfGroups,~] = size(channelGroup_mat);
    fprintf('Number of groups: %d\n',numOfGroups);
    File.Test.Groups = channelGroup_mat;
else
    File.Test.Groups = transpose(testChannel_arr);
end

end