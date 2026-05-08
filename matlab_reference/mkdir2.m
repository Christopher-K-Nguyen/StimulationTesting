function filepath_new = mkdir2(filepath)

filepath_old = filepath;
fileCount = 0;
status = false;
if exist(filepath,'dir')
    while true
        if exist(filepath,'dir')
            isSure = false;
            while ~isSure
                [~,foldername,~] = fileparts(filepath);
                prompt = sprintf('Already exists: %s.\n\tEnter 1 to replace, otherwise 0 to append: ',foldername);
                isReplacing = input(prompt);
                if isempty(isReplacing)
                    isReplacing = 1;
                end
                fprintf('\t');
                isSure = input('Enter 1 to confirm, otherwise 0: ');
                if isempty(isSure)
                    isSure = 1;
                end
            end
        else
            break;
        end
        if isReplacing
            [~,foldername,~] = fileparts(filepath);
            fprintf('\tNew folder: ');
            status = mkdir(filepath);
            fprintf('%s\n',foldername);
            break;
        else
            fileCount = fileCount + 1;
            filepath = sprintf('%s_%d',filepath_old,fileCount);
        end
    end
    if ~status
        fprintf('\tNew folder: ');
        mkdir(filepath);
        fprintf('%s\n',foldername);
    end
else
    mkdir(filepath);
end
filepath_new = filepath;